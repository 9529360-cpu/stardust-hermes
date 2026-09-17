"""Desktop personal-assistant facade over the durable Kanban work queue.

``background_task`` is intentionally a narrow intention-level surface. It owns no task state and
runs no workers itself; every operation delegates to the existing durable task kernel so task identity,
dependencies, retries, subscriptions, workspaces, cancellation, approvals, and dispatcher semantics
keep one owner. Read and mutation results project that kernel into personal-assistant vocabulary instead
of leaking worker/task-kernel implementation details back into the coordinator.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

from hermes_cli.config import cfg_get, load_config_readonly
from tools.background_task_state import PUBLIC_STATES, project_background_state, state_flags
from tools.registry import no_cache_check_fn, registry, tool_error


_READ_ACTIONS = frozenset({"status", "list", "approvals"})
_MUTATING_ACTIONS = frozenset({"start", "comment", "resume", "cancel", "approve", "deny"})
_ACTIONS = _READ_ACTIONS | _MUTATING_ACTIONS

# Public states with a one-to-one kernel phase can keep the low-level list query selective. States
# sharing one kernel phase (blocked/todo) are filtered after projection so approval/failure/dependency
# semantics stay truthful.
_KERNEL_FILTER_BY_PUBLIC_STATE = {
    "running": "running",
    "scheduled": "scheduled",
    "waiting_dependency": "todo",
    "waiting_confirmation": "blocked",
    "waiting_input": "blocked",
    "waiting_review": "review",
    "completed": "done",
    "failed": "blocked",
    "cancelled": "archived",
}
_SHARED_KERNEL_FILTER_STATES = frozenset({
    "queued", "waiting_dependency", "waiting_confirmation", "waiting_input", "needs_attention", "failed",
})


def _current_profile_name() -> str:
    """Session profile first, then the process/profile registry, finally the canonical default."""
    try:
        from gateway.session_context import get_session_env

        session_profile = str(get_session_env("HERMES_SESSION_PROFILE", "") or "").strip()
        if session_profile:
            return session_profile
    except Exception:
        pass
    env_profile = str(os.environ.get("HERMES_PROFILE") or "").strip()
    if env_profile:
        return env_profile
    try:
        from hermes_cli.profiles import get_active_profile_name

        return str(get_active_profile_name() or "default").strip() or "default"
    except Exception:
        return "default"


def _default_assignee() -> str:
    """Honor the operator's orchestration default; otherwise keep work on the active profile."""
    try:
        configured = str(
            cfg_get(load_config_readonly() or {}, "kanban", "default_assignee", default="") or ""
        ).strip()
        if configured:
            return configured
    except Exception:
        pass
    return _current_profile_name()


@no_cache_check_fn
def _check_background_task_mode() -> bool:
    """Match the existing orchestrator gate; capability remains session-scoped to Desktop."""
    try:
        from tools.kanban_tools import _check_kanban_orchestrator_mode

        return bool(_check_kanban_orchestrator_mode())
    except Exception:
        return False


def _dispatch_kanban(name: str, payload: dict[str, Any], kwargs: Mapping[str, Any]) -> str | dict:
    forwarded = {
        key: kwargs[key]
        for key in ("task_id", "session_id", "user_task")
        if key in kwargs and kwargs[key] is not None
    }
    return registry.dispatch(name, payload, **forwarded)


def _decode_result(result: str | dict) -> dict[str, Any] | None:
    if isinstance(result, dict):
        return result
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _encode_or_original(raw: str | dict, decoded: dict[str, Any] | None) -> str:
    if decoded is None:
        return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    return json.dumps(decoded, ensure_ascii=False)


def _state_projection(
    kernel_status: Any,
    *,
    dependencies: Any = (),
    pending_approvals: Any = (),
    events: Any = (),
    last_failure_error: Any = None,
) -> tuple[str, dict[str, bool]]:
    state = project_background_state(
        str(kernel_status or ""),
        dependencies=dependencies or (),
        pending_approvals=pending_approvals or (),
        events=events or (),
        last_failure_error=str(last_failure_error) if last_failure_error else None,
    )
    return state, state_flags(state)


def _start(args: dict[str, Any], kwargs: Mapping[str, Any]) -> str:
    title = str(args.get("title") or "").strip()
    if not title:
        return tool_error("background_task start requires a non-empty title")
    payload = {
        key: value
        for key, value in args.items()
        if key not in {"action"} and value is not None
    }
    payload["title"] = title
    if not str(payload.get("assignee") or "").strip():
        payload["assignee"] = _default_assignee()

    raw = _dispatch_kanban("kanban_create", payload, kwargs)
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return _encode_or_original(raw, decoded)

    state, flags = _state_projection(
        decoded.get("status"),
        dependencies=payload.get("parents") or (),
    )
    result = {
        "ok": bool(decoded.get("ok", True)),
        "kind": "background_task",
        "task_id": decoded.get("task_id"),
        "project_id": decoded.get("project_id"),
        "state": state,
        **flags,
        "subscribed": bool(decoded.get("subscribed")),
        "notification_mode": "automatic" if decoded.get("subscribed") else "manual",
    }
    return json.dumps(
        {key: value for key, value in result.items() if value is not None},
        ensure_ascii=False,
    )


def _require_task_id(args: Mapping[str, Any], action: str) -> str | None:
    task_id = str(args.get("task_id") or "").strip()
    return task_id or None


def _public_approval(approval: Mapping[str, Any]) -> dict[str, Any]:
    """Approval projection deliberately excludes args hashes/rule keys and other kernel metadata."""
    return {
        key: approval.get(key)
        for key in ("approval_id", "tool_name", "reason", "requested_at", "state")
        if approval.get(key) not in (None, "")
    }


def _pending_approvals(task_id: str) -> list[dict[str, Any]]:
    from tools.background_task_approval import list_approvals
    from tools.kanban_tools import _board

    with _board(None) as (_kb, conn):
        return [_public_approval(item) for item in list_approvals(conn, task_id, pending_only=True)]


def _latest_block_reason(events: list[Any]) -> str | None:
    for event in reversed(events):
        if not isinstance(event, Mapping) or event.get("kind") != "blocked":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and str(payload.get("reason") or "").strip():
            return str(payload.get("reason")).strip()
    return None


def _latest_run_summary(runs: list[Any]) -> dict[str, Any] | None:
    for run in reversed(runs):
        if not isinstance(run, Mapping):
            continue
        projected = {
            key: run.get(key)
            for key in ("status", "outcome", "summary", "error", "started_at", "ended_at")
            if run.get(key) not in (None, "")
        }
        if projected:
            return projected
    return None


def _status(args: Mapping[str, Any], kwargs: Mapping[str, Any]) -> str:
    task_id = str(args.get("task_id") or "").strip()
    raw = _dispatch_kanban("kanban_show", {"task_id": task_id}, kwargs)
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return _encode_or_original(raw, decoded)

    task = decoded.get("task") if isinstance(decoded.get("task"), Mapping) else {}
    events = decoded.get("events") if isinstance(decoded.get("events"), list) else []
    runs = decoded.get("runs") if isinstance(decoded.get("runs"), list) else []
    parents = decoded.get("parents") if isinstance(decoded.get("parents"), list) else []
    children = decoded.get("children") if isinstance(decoded.get("children"), list) else []
    pending = _pending_approvals(task_id)
    kernel_status = str(task.get("status") or "")
    state, flags = _state_projection(
        kernel_status,
        dependencies=parents,
        pending_approvals=pending,
        events=events,
        last_failure_error=task.get("last_failure_error"),
    )
    result: dict[str, Any] = {
        "ok": True,
        "kind": "background_task",
        "task_id": task.get("id") or task_id,
        "title": task.get("title"),
        "state": state,
        **flags,
        "priority": task.get("priority"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "result": task.get("result"),
        "dependencies": list(parents),
        "dependents": list(children),
        "blocked_reason": _latest_block_reason(events) if kernel_status == "blocked" else None,
        "pending_approvals": pending,
        "latest_attempt": _latest_run_summary(runs),
    }
    return json.dumps({key: value for key, value in result.items() if value is not None}, ensure_ascii=False)


def _precise_list_state(task_id: str, kwargs: Mapping[str, Any]) -> tuple[str, dict[str, bool]] | None:
    raw = _dispatch_kanban("kanban_show", {"task_id": task_id}, kwargs)
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return None
    task = decoded.get("task") if isinstance(decoded.get("task"), Mapping) else {}
    events = decoded.get("events") if isinstance(decoded.get("events"), list) else []
    parents = decoded.get("parents") if isinstance(decoded.get("parents"), list) else []
    pending = _pending_approvals(task_id)
    return _state_projection(
        task.get("status"),
        dependencies=parents,
        pending_approvals=pending,
        events=events,
        last_failure_error=task.get("last_failure_error"),
    )


def _project_list_task(
    task: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    *,
    precise: bool = False,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    kernel_status = str(task.get("status") or "")
    parent_count = int(task.get("parent_count") or 0)
    projection = _precise_list_state(task_id, kwargs) if precise and task_id else None
    if projection is None:
        if kernel_status == "blocked":
            # List summaries intentionally do not carry the event ledger. Without a precise lookup,
            # never guess whether blocked means consent, input, capability, or retry exhaustion.
            state, flags = "needs_attention", state_flags("needs_attention")
        else:
            state, flags = _state_projection(
                kernel_status,
                dependencies=(None,) * parent_count,
            )
    else:
        state, flags = projection
    return {
        key: value
        for key, value in {
            "task_id": task.get("id"),
            "title": task.get("title"),
            "state": state,
            **flags,
            "priority": task.get("priority"),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "dependency_count": task.get("parent_count"),
            "dependent_count": task.get("child_count"),
            "project_id": task.get("project_id"),
        }.items()
        if value is not None
    }


def _list(args: Mapping[str, Any], kwargs: Mapping[str, Any]) -> str:
    requested_state = str(args.get("state") or "").strip()
    if requested_state and requested_state not in PUBLIC_STATES:
        return tool_error(f"unknown background task state: {requested_state}")
    try:
        requested_limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        return tool_error("background_task list limit must be an integer")
    if requested_limit < 1 or requested_limit > 200:
        return tool_error("background_task list limit must be between 1 and 200")

    kernel_filter = _KERNEL_FILTER_BY_PUBLIC_STATE.get(requested_state)
    source_limit = 200 if requested_state in _SHARED_KERNEL_FILTER_STATES else requested_limit
    payload: dict[str, Any] = {"limit": source_limit}
    if kernel_filter:
        payload["status"] = kernel_filter

    raw = _dispatch_kanban("kanban_list", payload, kwargs)
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return _encode_or_original(raw, decoded)
    tasks = decoded.get("tasks") if isinstance(decoded.get("tasks"), list) else []
    precise = bool(requested_state)
    projected = [
        _project_list_task(task, kwargs, precise=precise)
        for task in tasks
        if isinstance(task, Mapping)
    ]
    if requested_state:
        projected = [task for task in projected if task.get("state") == requested_state]
    local_truncated = len(projected) > requested_limit
    projected = projected[:requested_limit]
    truncated = bool(decoded.get("truncated")) or local_truncated
    return json.dumps({
        "ok": True,
        "kind": "background_task_list",
        "tasks": projected,
        "count": len(projected),
        "limit": requested_limit,
        "truncated": truncated,
        "next_limit": (
            min(requested_limit * 2, 200)
            if truncated and requested_limit < 200
            else None
        ),
    }, ensure_ascii=False)


def _cancel_task(task_id: str) -> str:
    """Cancel through the durable kernel so a running worker is terminated after the archive commits."""
    from tools.kanban_tools import _board

    with _board(None) as (kb, conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return tool_error(f"unknown background task: {task_id}")
        if task.status == "archived":
            state, flags = _state_projection(task.status)
            return json.dumps({
                "ok": True,
                "kind": "background_task",
                "task_id": task_id,
                "state": state,
                **flags,
                "cancelled": False,
                "already_cancelled": True,
            }, ensure_ascii=False)
        if task.status == "done":
            return tool_error(f"background task {task_id} is already done and cannot be cancelled")
        if not kb.archive_task(conn, task_id):
            return tool_error(f"background task {task_id} could not be cancelled because its state changed")
        state, flags = _state_projection("archived")
        return json.dumps({
            "ok": True,
            "kind": "background_task",
            "task_id": task_id,
            "state": state,
            **flags,
            "cancelled": True,
        }, ensure_ascii=False)


def _kernel_event_projection(kb, conn, task_id: str) -> list[dict[str, Any]]:
    return [
        {"kind": event.kind, "payload": event.payload, "created_at": event.created_at}
        for event in kb.list_events(conn, task_id)
    ]


def _approval_operation(task_id: str, action: str, args: Mapping[str, Any]) -> str:
    from tools.background_task_approval import decide_task_approval, list_approvals
    from tools.kanban_tools import _board

    with _board(None) as (kb, conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return tool_error(f"unknown background task: {task_id}")
        if action == "approvals":
            approvals = list_approvals(conn, task_id, pending_only=True)
            state, flags = _state_projection(
                task.status,
                dependencies=kb.parent_ids(conn, task_id),
                pending_approvals=approvals,
                events=_kernel_event_projection(kb, conn, task_id),
                last_failure_error=task.last_failure_error,
            )
            return json.dumps({
                "ok": True,
                "kind": "background_task",
                "task_id": task_id,
                "state": state,
                **flags,
                "approvals": [_public_approval(item) for item in approvals],
            }, ensure_ascii=False)
        try:
            decision = decide_task_approval(
                conn,
                kb,
                task_id,
                decision=action,
                approval_id=str(args.get("approval_id") or "").strip(),
                reason=str(args.get("reason") or "").strip(),
            )
        except ValueError as exc:
            return tool_error(str(exc))

        task = kb.get_task(conn, task_id)
        if task is None:  # pragma: no cover - defensive after a successful durable decision
            return tool_error(f"background task disappeared after approval decision: {task_id}")
        pending = list_approvals(conn, task_id, pending_only=True)
        state, flags = _state_projection(
            task.status,
            dependencies=kb.parent_ids(conn, task_id),
            pending_approvals=pending,
            events=_kernel_event_projection(kb, conn, task_id),
            last_failure_error=task.last_failure_error,
        )
        result = {
            "ok": bool(decision.get("ok", True)),
            "kind": "background_task",
            "task_id": task_id,
            "approval_id": decision.get("approval_id"),
            "decision": decision.get("decision"),
            "already_decided": decision.get("already_decided"),
            "resumed": decision.get("resumed"),
            "state": state,
            **flags,
        }
        return json.dumps(
            {key: value for key, value in result.items() if value is not None},
            ensure_ascii=False,
        )


def _normalize_mutation(raw: str | dict, action: str) -> str:
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return _encode_or_original(raw, decoded)
    return json.dumps({
        "ok": bool(decoded.get("ok", True)),
        "kind": "background_task",
        "task_id": decoded.get("task_id"),
        "action": action,
    }, ensure_ascii=False)


def background_task(args: dict[str, Any], **kwargs: Any) -> str:
    """Translate personal-assistant background-task intent onto the durable queue."""
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return tool_error(f"background_task action must be one of: {', '.join(sorted(_ACTIONS))}")
    if action == "start":
        return _start(args, kwargs)
    if action == "list":
        return _list(args, kwargs)

    task_id = _require_task_id(args, action)
    if not task_id:
        return tool_error(f"background_task {action} requires task_id")
    if action == "status":
        return _status(args, kwargs)
    if action == "comment":
        body = str(args.get("body") or "").strip()
        if not body:
            return tool_error("background_task comment requires a non-empty body")
        raw = _dispatch_kanban("kanban_comment", {"task_id": task_id, "body": body}, kwargs)
        return _normalize_mutation(raw, action)
    if action == "cancel":
        return _cancel_task(task_id)
    if action in {"approvals", "approve", "deny"}:
        return _approval_operation(task_id, action, args)

    raw = _dispatch_kanban("kanban_unblock", {"task_id": task_id}, kwargs)
    decoded = _decode_result(raw)
    if decoded is None or decoded.get("error"):
        return _encode_or_original(raw, decoded)
    status_result = _decode_result(_status({"task_id": task_id}, kwargs))
    if status_result is None or status_result.get("error"):
        return _normalize_mutation(raw, action)
    status_result["action"] = action
    return json.dumps(status_result, ensure_ascii=False)


BACKGROUND_TASK_SCHEMA = {
    "name": "background_task",
    "description": (
        "Manage durable personal-assistant background work. Use start only when the user should not have to wait and "
        "the work must survive chat/app restarts, retry safely, or participate in dependency/review flows. Answer "
        "ordinary questions directly; keep work that can finish in this live turn in the live session instead of "
        "creating a background task. Use status/list to inspect durable work. Returned state values are personal-" 
        "assistant states such as queued, running, waiting_confirmation, waiting_dependency, completed, failed, and "
        "cancelled; do not reason from internal scheduler phases. If a high-risk background action pauses for user "
        "consent, use approvals to inspect the pending request and approve/deny to record the user's decision; approve "
        "resumes the task and the grant is valid exactly once for the same resolved tool call. Use comment to add "
        "durable context, resume for ordinary non-approval blockers, and cancel to stop pending/running work. Use "
        "cronjob_manage, not this tool, for scheduled/recurring triggers. The returned subscribed/notification_mode "
        "fields are authoritative: promise automatic completion reporting only when subscribed=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "status", "list", "approvals", "approve", "deny", "comment", "resume", "cancel"],
                "description": "Operation to perform on durable background work.",
            },
            "title": {"type": "string", "description": "Short outcome title; required for start."},
            "body": {
                "type": "string",
                "description": "For start: self-contained task specification. For comment: durable comment text.",
            },
            "task_id": {
                "type": "string",
                "description": "Task id for status/approvals/approve/deny/comment/resume/cancel.",
            },
            "approval_id": {
                "type": "string",
                "description": "Optional pending approval id for approve/deny; omit to decide the latest pending request.",
            },
            "reason": {
                "type": "string",
                "description": "Optional user-provided reason when denying a pending background approval.",
            },
            "assignee": {
                "type": "string",
                "description": (
                    "Optional specialist profile. Omit for normal personal-assistant work: Stardust uses the configured "
                    "orchestration default, then the current Desktop profile."
                ),
            },
            "parents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional prerequisite background-task ids for start; work waits until all parents finish.",
            },
            "project": {"type": "string", "description": "Optional Stardust project id/slug for a managed worktree."},
            "priority": {"type": "integer", "description": "Optional dispatch priority; higher runs sooner."},
            "goal_mode": {
                "type": "boolean",
                "description": "For open-ended work, keep the worker iterating until its completion goal is met.",
            },
            "goal_max_turns": {"type": "integer", "description": "Optional goal-mode continuation budget."},
            "max_runtime_seconds": {"type": "integer", "description": "Optional per-attempt runtime cap."},
            "idempotency_key": {
                "type": "string",
                "description": "Optional retry-safe key; repeated start calls reuse the non-cancelled task.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional specialist skill names to force-load in the worker.",
            },
            "completion_contract": {
                "type": "string",
                "description": "Optional completion contract, e.g. local-only, OWNER/REPO, or an exact PR URL.",
            },
            "workspace_kind": {
                "type": "string", "enum": ["scratch", "dir", "worktree"],
                "description": "Optional workspace flavor for start.",
            },
            "workspace_path": {"type": "string", "description": "Absolute path for dir/worktree workspace."},
            "state": {
                "type": "string",
                "enum": sorted(PUBLIC_STATES),
                "description": "Optional personal-assistant state filter for list.",
            },
            "limit": {"type": "integer", "description": "Optional list row limit (default 50, max 200)."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="background_task",
    toolset="assistant_orchestration",
    schema=BACKGROUND_TASK_SCHEMA,
    handler=lambda args, **kw: background_task(args, **kw),
    check_fn=_check_background_task_mode,
    description="Durable personal-assistant background task control",
    emoji="🗂️",
)
