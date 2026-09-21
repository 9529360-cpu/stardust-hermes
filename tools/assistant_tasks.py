"""Core durable task intake for the personal-assistant surface.

This is deliberately a thin adapter over the existing Kanban authority. It does not own
task lifecycle state. The parent agent uses it to register independent work that should
continue after the current turn or process, and to recall the user's durable work later
across conversation and active-board switches.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from tools.registry import no_cache_check_fn, registry, tool_error


MAX_TASKS_PER_CALL = 8
MAX_TITLE_CHARS = 240
MAX_INSTRUCTION_CHARS = 16_000
MAX_RESUME_MESSAGE_CHARS = 16_000
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


def _idempotency_scope_token(owner_key: str, request_id: str) -> str:
    """Replay-stable token without a reusable user fingerprint.

    Board archives preserve idempotency keys. Hashing the owner by itself would
    leave the same pseudonymous marker on every task from one messaging user
    (and low-entropy numeric IDs could be dictionary-tested). Bind the owner to
    the high-entropy tool-call id so retries of one call remain stable while
    unrelated calls cannot be linked by a persistent owner token.
    """
    material = f"{owner_key}\0{request_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _active_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _assistant_tasks_context_allowed() -> bool:
    """Only parent/user sessions may access the personal durable-task index.

    Kanban workers already have lineage-scoped `kanban_create` for follow-up
    work, and delegated children must return results to their parent. Exposing
    this user-wide intake/index inside either context would let a scoped worker
    inspect or enqueue unrelated durable work.
    """
    from agent.delegation_context import (
        is_delegated_child_process_context,
        is_dispatcher_owned_worker_context,
    )

    if is_delegated_child_process_context():
        return False
    return not (
        os.environ.get("HERMES_KANBAN_TASK")
        and is_dispatcher_owned_worker_context()
    )


def _resolve_owner_key() -> Optional[str]:
    """Stable task owner for cross-conversation recall.

    Local surfaces (Desktop/TUI/CLI/API on the user's machine) share one personal
    owner so conversation/session changes within the active profile do not hide work.
    Human messaging surfaces
    require a stable platform user id and fail closed when it is unavailable.
    """
    from gateway.session_context import get_session_env, session_is_messaging_surface

    if not session_is_messaging_surface():
        return "local"
    platform = (
        get_session_env("HERMES_SESSION_PLATFORM", "")
        or get_session_env("HERMES_SESSION_SOURCE", "")
    ).strip().lower()
    principal = (
        get_session_env("HERMES_SESSION_USER_ID_ALT", "")
        or get_session_env("HERMES_SESSION_USER_ID", "")
    ).strip()
    if not platform or not principal:
        return None
    return f"messaging:{platform}:{principal}"


def _assistant_board_slugs() -> list[str]:
    """Active Kanban boards, de-duplicated by their physical database.

    Durable assistant ownership is broader than the UI's currently-selected
    board. Creation stays board-scoped, but later status recall must find the
    user's work after a board/project switch. HERMES_KANBAN_DB can make
    multiple slugs alias one DB, so collapse those aliases before querying.
    """
    from hermes_cli import kanban_db as kb

    # Dispatcher workers and explicitly pinned CLI runtimes are intentionally
    # scoped to one physical board DB. Enumerating metadata while the pin is
    # active would make every slug resolve to that same DB and mislabel it.
    if os.environ.get("HERMES_KANBAN_DB", "").strip():
        return [kb.get_current_board()]

    # Let enumeration failures reach the caller. Falling back silently to the
    # current board would make a cross-board snapshot look complete when it is not.
    boards = kb.list_boards(include_archived=False)

    unique: dict[str, str] = {}
    for meta in boards:
        slug = str((meta or {}).get("slug") or kb.DEFAULT_BOARD)
        raw_path = (meta or {}).get("db_path")
        try:
            identity = str(
                (Path(str(raw_path)).expanduser() if raw_path else kb.kanban_db_path(slug))
                .resolve()
            )
        except Exception:
            identity = f"slug:{slug}"
        unique.setdefault(identity, slug)
    return list(unique.values()) or [kb.get_current_board()]


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
    owner_key: str,
) -> str:
    if not isinstance(tasks, list) or not tasks:
        return tool_error("assistant_tasks create requires a non-empty tasks list")
    if len(tasks) > MAX_TASKS_PER_CALL:
        return tool_error(
            f"assistant_tasks accepts at most {MAX_TASKS_PER_CALL} independent tasks per call"
        )

    from tools.kanban_tools import _handle_create

    default_assignee = _active_profile_name()
    # Tool runtimes normally provide a stable call id. If an internal/manual
    # caller does not, use an unlinkable one-shot scope rather than a fixed
    # fallback that would collapse every later task for the same owner.
    scope = str(request_id or "").strip() or uuid.uuid4().hex
    sid = str(session_id or "").strip()
    owner = str(owner_key or "").strip()
    replay_token = _idempotency_scope_token(owner, scope)
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
            "idempotency_key": f"assistant:{replay_token}:{index}",
            "_assistant_owner_key": owner,
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
    owner_key: str,
) -> str:
    from tools.kanban_tools import _board

    wanted = {
        str(task_id).strip()
        for task_id in (task_ids if isinstance(task_ids, list) else [])[:MAX_LIST_LIMIT]
        if str(task_id).strip()
    }
    max_items = _safe_limit(limit)
    projected: list[dict[str, Any]] = []
    board_errors: list[dict[str, str]] = []
    # Creation remains board-scoped, but personal-assistant recall is user-scoped:
    # switching projects/boards must not make already-handed-off work disappear.
    try:
        boards = _assistant_board_slugs()
    except Exception as exc:
        # Keep the current board useful, but explicitly mark the snapshot partial.
        from hermes_cli import kanban_db as kb

        boards = [kb.get_current_board()]
        board_errors.append({"board": "*", "error": type(exc).__name__})
    for board in boards:
        try:
            with _board(board) as (kb, conn):
                # Read a bounded recent superset from every active board, then merge
                # globally. The Kanban DBs remain authoritative; no shadow index.
                if wanted:
                    # Explicit task handles must not disappear merely because they are
                    # older than the recent-work window. Fetch them directly and prove
                    # ownership before projecting anything.
                    rows = []
                    for task_id in wanted:
                        task = kb.get_task(conn, task_id)
                        if task is not None and task.assistant_owner_key == owner_key:
                            rows.append(task)
                else:
                    rows = kb.list_tasks(
                        conn,
                        assistant_owner_key=owner_key,
                        include_archived=False,
                        limit=max(200, max_items * 4),
                        order_by="activity",
                    )
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
                        "board": board,
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
        except Exception as exc:
            # Preserve useful results from healthy boards, but never imply the
            # snapshot was complete when one authority could not be read.
            board_errors.append({
                "board": board,
                "error": type(exc).__name__,
            })

    projected.sort(
        key=lambda row: (
            int(row.get("completed_at") or row.get("started_at") or row.get("created_at") or 0),
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
            "partial": bool(board_errors),
            "board_errors": board_errors,
        },
        ensure_ascii=False,
    )



def _resume_task(
    *,
    task_id: Any,
    user_message: Optional[str],
    owner_key: str,
) -> str:
    """Persist the current user's input, then resume one owned blocked task.

    Mutation is deliberately fail-closed across boards: unlike a read-only list,
    a partial board scan is not safe enough to establish unique ownership.
    """
    from tools.kanban_tools import _board

    tid = str(task_id or "").strip()
    if not tid:
        return tool_error("assistant_tasks resume requires task_id")
    message = _bounded_text(user_message, MAX_RESUME_MESSAGE_CHARS)
    if not message:
        return tool_error(
            "assistant_tasks resume requires the current user message; "
            "do not infer approval or input from memory, prior chats, or assistant text"
        )

    try:
        boards = _assistant_board_slugs()
    except Exception as exc:
        return tool_error(
            "assistant_tasks cannot safely resume while board discovery is incomplete",
            error_type=type(exc).__name__,
        )

    matches: list[tuple[str, Any]] = []
    for board in boards:
        try:
            with _board(board) as (kb, conn):
                task = kb.get_task(conn, tid)
                if task is not None and task.assistant_owner_key == owner_key:
                    matches.append((board, task))
        except Exception as exc:
            return tool_error(
                "assistant_tasks cannot safely resume while a board is unreadable",
                board=board,
                error_type=type(exc).__name__,
            )

    if not matches:
        return tool_error("assistant_tasks task not found for the current owner")
    if len(matches) != 1:
        return tool_error(
            "assistant_tasks task id is ambiguous across boards; refusing to mutate",
            boards=[board for board, _task in matches],
        )

    board, task = matches[0]
    if task.status != "blocked":
        return tool_error(
            "assistant_tasks resume only accepts a blocked task",
            task_id=tid,
            board=board,
            status=task.status,
        )

    # Persist the human's CURRENT turn before making the task runnable. A worker
    # may be claimed immediately after unblock; reversing these writes would let
    # it restart without the approval/input that justified the resume.
    try:
        with _board(board) as (kb, conn):
            current = kb.get_task(conn, tid)
            if current is None or current.assistant_owner_key != owner_key:
                return tool_error("assistant_tasks task ownership changed before resume")
            if current.status != "blocked":
                return tool_error(
                    "assistant_tasks task is no longer blocked",
                    task_id=tid,
                    board=board,
                    status=current.status,
                )
            comment_id = kb.add_assistant_user_input(
                conn,
                tid,
                message,
            )
            resumed = kb.unblock_task(conn, tid)
            landed = kb.get_task(conn, tid)
    except Exception as exc:
        return tool_error(
            "assistant_tasks could not record user input and resume the task",
            task_id=tid,
            board=board,
            error_type=type(exc).__name__,
        )

    if not resumed:
        # The input is still useful durable evidence even if another actor raced
        # the status transition. Say so explicitly instead of pretending resume
        # was atomic across the two existing Kanban write APIs.
        return tool_error(
            "assistant_tasks recorded the user input but the task could not be resumed",
            task_id=tid,
            board=board,
            comment_id=comment_id,
            input_recorded=True,
            status=(landed.status if landed else None),
        )

    return json.dumps(
        {
            "ok": True,
            "task_id": tid,
            "board": board,
            "status": landed.status if landed else "ready",
            "comment_id": comment_id,
            "input_recorded": True,
        },
        ensure_ascii=False,
    )

def assistant_tasks_tool(
    *,
    action: str = "create",
    tasks: Any = None,
    include_completed: bool = True,
    limit: Any = 20,
    task_ids: Any = None,
    task_id: Any = None,
    user_message: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    owner_key: Optional[str] = None,
) -> str:
    """Create durable independent work or query the durable assistant task queue."""
    if not _assistant_tasks_context_allowed():
        return tool_error(
            "assistant_tasks is only available to parent user sessions; "
            "scoped workers must return results to their parent or use lineage-scoped kanban tools"
        )
    action = str(action or "create").strip().lower()
    owner = str(owner_key or _resolve_owner_key() or "").strip()
    if not owner:
        return tool_error(
            "assistant_tasks requires a stable user identity on human messaging surfaces"
        )
    if action == "create":
        return _create_tasks(
            tasks,
            session_id=session_id,
            request_id=request_id,
            owner_key=owner,
        )
    if action == "list":
        return _list_tasks(
            include_completed=bool(include_completed),
            limit=limit,
            task_ids=task_ids,
            owner_key=owner,
        )
    if action == "resume":
        return _resume_task(
            task_id=task_id,
            user_message=user_message,
            owner_key=owner,
        )
    return tool_error("assistant_tasks action must be 'create', 'list', or 'resume'")


@no_cache_check_fn
def check_assistant_tasks_requirements() -> bool:
    return _assistant_tasks_context_allowed()


ASSISTANT_TASKS_SCHEMA = {
    "name": "assistant_tasks",
    "description": (
        "Durable background work for the personal assistant. Use action=create when the user gives "
        "one or more independent action requests that should continue after this turn or survive a "
        "restart. Create one task per independent outcome instead of serializing unrelated work in "
        "the parent conversation. The existing Kanban dispatcher owns execution, retries, recovery, "
        "blocking, and completion notifications. Use action=list when the user asks what long-running "
        "work is still running, blocked, waiting for review, or recently completed; the lookup follows "
        "the stable assistant owner across conversations and active Kanban boards. Use action=resume only "
        "when the CURRENT user message explicitly supplies the requested input or approval for one blocked "
        "task; the runtime records that exact user message before resuming. Never infer approval from prior "
        "chat history, memory, or assistant text. Do NOT use this "
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
                "enum": ["create", "list", "resume"],
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
                "default": True,
                "description": (
                    "With action=list, include recently done tasks as well as active/attention tasks. "
                    "Leave true for 'how are my tasks doing?'; set false only for an active-only view."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIST_LIMIT,
                "default": 20,
            },
            "task_ids": {
                "type": "array",
                "maxItems": MAX_LIST_LIMIT,
                "items": {"type": "string"},
                "description": "With action=list, optionally restrict the query to these task ids.",
            },
            "task_id": {
                "type": "string",
                "description": "With action=resume, the single owned blocked task to resume from the current user message.",
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
        include_completed=args.get("include_completed", True),
        limit=args.get("limit", 20),
        task_ids=args.get("task_ids"),
        task_id=args.get("task_id"),
        user_message=kw.get("user_message"),
        session_id=kw.get("session_id"),
        request_id=kw.get("tool_call_id") or kw.get("task_id"),
        owner_key=kw.get("assistant_owner_key"),
    ),
    emoji="🧭",
)
