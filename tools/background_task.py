"""Desktop personal-assistant facade over the durable Kanban work queue.

``background_task`` is intentionally a narrow intention-level surface. It owns no task state and
runs no workers itself; every operation delegates to the existing Kanban kernel so task identity,
dependencies, retries, subscriptions, workspaces, cancellation, approvals, and dispatcher semantics
keep one owner.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

from hermes_cli.config import cfg_get, load_config_readonly
from tools.registry import no_cache_check_fn, registry, tool_error


_READ_ACTIONS = frozenset({"status", "list", "approvals"})
_MUTATING_ACTIONS = frozenset({"start", "comment", "resume", "cancel", "approve", "deny"})
_ACTIONS = _READ_ACTIONS | _MUTATING_ACTIONS


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
        return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    # ``subscribed`` is the durable delivery truth. Keep it verbatim and add a semantic alias so
    # the coordinator never promises a completion notification when no route was actually stored.
    decoded["kind"] = "background_task"
    decoded["notification_mode"] = "automatic" if decoded.get("subscribed") else "manual"
    return json.dumps(decoded, ensure_ascii=False)


def _require_task_id(args: Mapping[str, Any], action: str) -> str | None:
    task_id = str(args.get("task_id") or "").strip()
    return task_id or None


def _cancel_task(task_id: str) -> str:
    """Cancel through the Kanban kernel so a running worker is terminated after the archive commits."""
    from tools.kanban_tools import _board

    with _board(None) as (kb, conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return tool_error(f"unknown background task: {task_id}")
        if task.status == "archived":
            return json.dumps({
                "ok": True,
                "kind": "background_task",
                "task_id": task_id,
                "status": "archived",
                "cancelled": False,
                "already_cancelled": True,
            }, ensure_ascii=False)
        if task.status == "done":
            return tool_error(f"background task {task_id} is already done and cannot be cancelled")
        if not kb.archive_task(conn, task_id):
            return tool_error(f"background task {task_id} could not be cancelled because its state changed")
        return json.dumps({
            "ok": True,
            "kind": "background_task",
            "task_id": task_id,
            "status": "archived",
            "cancelled": True,
        }, ensure_ascii=False)


def _approval_operation(task_id: str, action: str, args: Mapping[str, Any]) -> str:
    from tools.background_task_approval import decide_task_approval, list_approvals
    from tools.kanban_tools import _board

    with _board(None) as (kb, conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return tool_error(f"unknown background task: {task_id}")
        if action == "approvals":
            return json.dumps({
                "ok": True,
                "kind": "background_task",
                "task_id": task_id,
                "status": task.status,
                "approvals": list_approvals(conn, task_id, pending_only=True),
            }, ensure_ascii=False)
        try:
            result = decide_task_approval(
                conn,
                kb,
                task_id,
                decision=action,
                approval_id=str(args.get("approval_id") or "").strip(),
                reason=str(args.get("reason") or "").strip(),
            )
        except ValueError as exc:
            return tool_error(str(exc))
        result["kind"] = "background_task"
        return json.dumps(result, ensure_ascii=False)


def background_task(args: dict[str, Any], **kwargs: Any) -> str:
    """Translate personal-assistant background-task intent onto the durable queue."""
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return tool_error(f"background_task action must be one of: {', '.join(sorted(_ACTIONS))}")
    if action == "start":
        return _start(args, kwargs)
    if action == "list":
        payload = {key: value for key, value in args.items() if key in {"status", "limit"} and value is not None}
        return _dispatch_kanban("kanban_list", payload, kwargs)

    task_id = _require_task_id(args, action)
    if not task_id:
        return tool_error(f"background_task {action} requires task_id")
    if action == "status":
        return _dispatch_kanban("kanban_show", {"task_id": task_id}, kwargs)
    if action == "comment":
        body = str(args.get("body") or "").strip()
        if not body:
            return tool_error("background_task comment requires a non-empty body")
        return _dispatch_kanban("kanban_comment", {"task_id": task_id, "body": body}, kwargs)
    if action == "cancel":
        return _cancel_task(task_id)
    if action in {"approvals", "approve", "deny"}:
        return _approval_operation(task_id, action, args)
    return _dispatch_kanban("kanban_unblock", {"task_id": task_id}, kwargs)


BACKGROUND_TASK_SCHEMA = {
    "name": "background_task",
    "description": (
        "Manage durable personal-assistant background work. Use start when the user should not have to wait and the "
        "work must survive chat/app restarts, retry safely, or participate in dependency/review flows. Use status/list "
        "to inspect durable work. If a high-risk background action pauses for user consent, use approvals to inspect "
        "the pending request and approve/deny to record the user's decision; approve resumes the task and the grant is "
        "valid exactly once for the same resolved tool call. Use comment to add durable context, resume for ordinary "
        "non-approval blockers, and cancel to stop pending/running work. Do not use this for ordinary questions, work "
        "that will finish in the current turn, or scheduled/recurring triggers. The returned subscribed/notification_mode "
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
                    "kanban.default_assignee, then the current Desktop profile."
                ),
            },
            "parents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional prerequisite task ids for start; work waits until all parents finish.",
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
                "description": "Optional retry-safe key; repeated start calls reuse the non-archived task.",
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
            "status": {
                "type": "string",
                "enum": ["triage", "todo", "ready", "running", "blocked", "done", "archived"],
                "description": "Optional status filter for list.",
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
