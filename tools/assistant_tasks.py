"""Core durable task intake for the personal-assistant surface.

This is deliberately a thin adapter over the existing Kanban authority. It does not own
task lifecycle state. The parent agent uses it to register independent work that should
continue after the current turn or process, and to query the same durable board later.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from tools.registry import registry, tool_error


MAX_TASKS_PER_CALL = 8
MAX_TITLE_CHARS = 240
MAX_INSTRUCTION_CHARS = 16_000
MAX_LIST_LIMIT = 50
_ATTENTION_STATUSES = {"blocked", "review", "triage"}
_TERMINAL_STATUSES = {"done", "archived"}

_APPROVAL_NOTE = (
    "\n\n[User-control boundary]\n"
    "This task may culminate in an external, financial, destructive, credential, permission, "
    "publication, message-send, booking, or other irreversible side effect. You may research, "
    "prepare, compare options, and fill reversible drafts. Immediately before the final external "
    "commit, call kanban_block(kind=\"needs_input\", reason=...) with the exact proposed action "
    "and any material price/recipient/destination details, then stop. Resume the commit only after "
    "the user explicitly approves it."
)


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _active_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _safe_limit(value: Any) -> int:
    try:
        return max(1, min(int(value or 20), MAX_LIST_LIMIT))
    except (TypeError, ValueError):
        return 20


def _create_tasks(
    tasks: Any,
    *,
    session_id: Optional[str],
    request_id: Optional[str],
) -> str:
    if not isinstance(tasks, list) or not tasks:
        return tool_error("assistant_tasks create requires a non-empty tasks list")
    if len(tasks) > MAX_TASKS_PER_CALL:
        return tool_error(
            f"assistant_tasks accepts at most {MAX_TASKS_PER_CALL} independent tasks per call"
        )

    from tools.kanban_tools import _handle_create

    default_assignee = _active_profile_name()
    scope = str(request_id or "request").strip() or "request"
    sid = str(session_id or "").strip()
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for index, raw in enumerate(tasks):
        if not isinstance(raw, dict):
            failed.append({"index": index, "error": "task must be an object"})
            continue
        title = _bounded_text(raw.get("title"), MAX_TITLE_CHARS)
        instruction = _bounded_text(raw.get("instruction"), MAX_INSTRUCTION_CHARS)
        if not title or not instruction:
            failed.append({"index": index, "error": "title and instruction are required"})
            continue

        approval_required = bool(raw.get("approval_required"))
        body = instruction + (_APPROVAL_NOTE if approval_required else "")
        args: dict[str, Any] = {
            "title": title,
            "body": body,
            "assignee": str(raw.get("assignee") or default_assignee).strip(),
            "priority": raw.get("priority", 0),
            "goal_mode": bool(raw.get("continuous")),
            "idempotency_key": f"assistant:{sid or 'session'}:{scope}:{index}",
        }
        if sid:
            args["session_id"] = sid
        if raw.get("goal_max_turns") is not None:
            args["goal_max_turns"] = raw.get("goal_max_turns")
        if "project" in raw:
            args["project"] = raw.get("project")
        if raw.get("workspace_kind"):
            args["workspace_kind"] = raw.get("workspace_kind")
        if raw.get("workspace_path"):
            args["workspace_path"] = raw.get("workspace_path")

        try:
            result = json.loads(_handle_create(args))
        except Exception as exc:
            failed.append({"index": index, "title": title, "error": str(exc)})
            continue
        if not result.get("ok"):
            failed.append({
                "index": index,
                "title": title,
                "error": result.get("error") or "task creation failed",
            })
            continue
        created.append({
            "index": index,
            "task_id": result.get("task_id"),
            "title": title,
            "status": result.get("status"),
            "assignee": args["assignee"],
            "project_id": result.get("project_id"),
            "workspace_kind": result.get("workspace_kind"),
            "subscribed": bool(result.get("subscribed")),
            "approval_required": approval_required,
            "continuous": bool(raw.get("continuous")),
        })

    return json.dumps(
        {
            "ok": bool(created) and not failed,
            "created": created,
            "failed": failed,
            "summary": {
                "requested": len(tasks),
                "created": len(created),
                "failed": len(failed),
            },
        },
        ensure_ascii=False,
    )


def _list_tasks(
    *,
    include_completed: bool,
    limit: Any,
    task_ids: Any,
) -> str:
    from tools.kanban_tools import _board

    wanted = {
        str(task_id).strip()
        for task_id in (task_ids if isinstance(task_ids, list) else [])
        if str(task_id).strip()
    }
    max_items = _safe_limit(limit)
    with _board(None) as (kb, conn):
        # Read a bounded superset, then sort for assistant-facing recency. The board
        # remains the authority; this adapter keeps no index or task cache of its own.
        rows = kb.list_tasks(
            conn,
            include_archived=False,
            limit=max(200, max_items * 4),
        )
        projected: list[dict[str, Any]] = []
        for task in rows:
            if wanted and task.id not in wanted:
                continue
            if not include_completed and task.status in _TERMINAL_STATUSES:
                continue
            run = kb.latest_run(conn, task.id)
            detail = _bounded_text(
                (run.summary if run else None) or task.last_failure_error or task.result,
                600,
            )
            projected.append({
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "assignee": task.assignee,
                "project_id": task.project_id,
                "workspace_kind": task.workspace_kind,
                "workspace_path": task.workspace_path,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "block_kind": task.block_kind,
                "needs_attention": task.status in _ATTENTION_STATUSES,
                "detail": detail,
            })
        projected.sort(
            key=lambda row: (
                int(row.get("started_at") or row.get("completed_at") or row.get("created_at") or 0),
                str(row.get("task_id") or ""),
            ),
            reverse=True,
        )
        projected = projected[:max_items]

    return json.dumps(
        {
            "ok": True,
            "tasks": projected,
            "count": len(projected),
            "include_completed": bool(include_completed),
        },
        ensure_ascii=False,
    )


def assistant_tasks_tool(
    *,
    action: str = "create",
    tasks: Any = None,
    include_completed: bool = False,
    limit: Any = 20,
    task_ids: Any = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> str:
    """Create durable independent work or query the durable assistant task queue."""
    action = str(action or "create").strip().lower()
    if action == "create":
        return _create_tasks(tasks, session_id=session_id, request_id=request_id)
    if action == "list":
        return _list_tasks(
            include_completed=bool(include_completed),
            limit=limit,
            task_ids=task_ids,
        )
    return tool_error("assistant_tasks action must be 'create' or 'list'")


def check_assistant_tasks_requirements() -> bool:
    return True


ASSISTANT_TASKS_SCHEMA = {
    "name": "assistant_tasks",
    "description": (
        "Durable background work for the personal assistant. Use action=create when the user gives "
        "one or more independent action requests that should continue after this turn or survive a "
        "restart. Create one task per independent outcome instead of serializing unrelated work in "
        "the parent conversation. The existing Kanban dispatcher owns execution, retries, recovery, "
        "blocking, and completion notifications. Use action=list when the user asks what long-running "
        "work is still running, blocked, waiting for review, or recently completed. Do NOT use this "
        "for ordinary answers or tiny foreground actions that can be completed immediately. Set "
        "approval_required=true for any task that may culminate in a purchase, booking, message/send, "
        "publication, destructive change, credential/permission change, or other irreversible "
        "external side effect; that worker must prepare first and block for explicit user approval "
        "immediately before the final commit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list"],
                "default": "create",
            },
            "tasks": {
                "type": "array",
                "maxItems": MAX_TASKS_PER_CALL,
                "description": "Independent durable tasks to create.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short user-facing outcome label."},
                        "instruction": {
                            "type": "string",
                            "description": "Complete self-contained instruction the background worker should execute.",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Optional profile name. Omit to run with the current assistant profile.",
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional Stardust project id/slug. Omit when the work is not project-scoped.",
                        },
                        "workspace_kind": {
                            "type": "string",
                            "enum": ["scratch", "dir", "worktree"],
                        },
                        "workspace_path": {"type": "string"},
                        "priority": {"type": "integer", "default": 0},
                        "continuous": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Use the durable goal loop for open-ended work that should keep making progress "
                                "across multiple turns until done or it reaches a human/review boundary."
                            ),
                        },
                        "goal_max_turns": {"type": "integer", "minimum": 1},
                        "approval_required": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "The task may research/prepare autonomously but must block for explicit user "
                                "approval immediately before its final external/financial/irreversible commit."
                            ),
                        },
                    },
                    "required": ["title", "instruction"],
                },
            },
            "include_completed": {
                "type": "boolean",
                "default": False,
                "description": "With action=list, include done tasks as well as active/attention tasks.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIST_LIMIT,
                "default": 20,
            },
            "task_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "With action=list, optionally restrict the query to these task ids.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="assistant_tasks",
    toolset="assistant_tasks",
    schema=ASSISTANT_TASKS_SCHEMA,
    check_fn=check_assistant_tasks_requirements,
    handler=lambda args, **kw: assistant_tasks_tool(
        action=args.get("action", "create"),
        tasks=args.get("tasks"),
        include_completed=args.get("include_completed", False),
        limit=args.get("limit", 20),
        task_ids=args.get("task_ids"),
        session_id=kw.get("session_id"),
        request_id=kw.get("tool_call_id") or kw.get("task_id"),
    ),
    emoji="🧭",
)
