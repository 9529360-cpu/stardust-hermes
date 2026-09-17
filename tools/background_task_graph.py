"""Atomic task-graph creation for the personal-assistant background-task facade.

The public surface uses logical task keys and ``depends_on`` keys. This module translates those into
one transaction in the existing durable task kernel; it owns no scheduler, queue, or worker state itself.
Independent nodes become runnable together, dependency-gated nodes wait until their parents complete,
and every created node is subscribed to the originating assistant session when possible.
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Any, Mapping

from tools.background_task_state import project_background_state
from tools.registry import no_cache_check_fn, registry, tool_error


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_GRAPH_TASKS = 32
_DEFAULT_FINAL_KEY = "final-report"
_DEFAULT_FINAL_TITLE = "Summarize background work"
_DEFAULT_FINAL_BODY = (
    "Review the completed parent task handoffs and produce one concise completion report for the user. "
    "State what finished, any failures or blockers, important artifacts/results, and any follow-up the user still "
    "needs to decide. Do not redo work already completed by parent tasks."
)


def _text(value: Any, *, limit: int = 10000) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _with_final_report(raw_tasks: Any, final_report: Any) -> tuple[Any, str | None]:
    """Append one fan-in reporter that depends on all current graph leaves."""
    if final_report is None:
        return raw_tasks, None
    if not isinstance(final_report, Mapping):
        raise ValueError("final_report must be an object when provided")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return raw_tasks, None

    copied = [dict(item) if isinstance(item, Mapping) else item for item in raw_tasks]
    keys = [
        _text(item.get("key"), limit=64)
        for item in copied
        if isinstance(item, Mapping)
    ]
    referenced: set[str] = set()
    for item in copied:
        if isinstance(item, Mapping):
            referenced.update(_string_list(item.get("depends_on"), "tasks[].depends_on"))
    leaves = [key for key in keys if key and key not in referenced]

    final_key = _text(final_report.get("key"), limit=64) or _DEFAULT_FINAL_KEY
    if final_key in keys:
        raise ValueError(f"final_report key conflicts with an existing task key: {final_key}")
    explicit_dependencies = _string_list(final_report.get("depends_on"), "final_report.depends_on")
    depends_on = list(dict.fromkeys([*leaves, *explicit_dependencies]))
    final_spec = dict(final_report)
    final_spec.update(
        key=final_key,
        title=_text(final_report.get("title"), limit=500) or _DEFAULT_FINAL_TITLE,
        body=_text(final_report.get("body")) or _DEFAULT_FINAL_BODY,
        depends_on=depends_on,
    )
    copied.append(final_spec)
    return copied, final_key


def _normalize_tasks(raw_tasks: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("background_task_graph requires a non-empty tasks list")
    if len(raw_tasks) > _MAX_GRAPH_TASKS:
        raise ValueError(f"background_task_graph supports at most {_MAX_GRAPH_TASKS} tasks")

    specs: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, Mapping):
            raise ValueError(f"tasks[{index}] must be an object")
        key = _text(raw.get("key"), limit=64)
        if not _KEY_RE.match(key):
            raise ValueError(
                f"tasks[{index}].key must be 1-64 ASCII letters/numbers/dot/dash/underscore, starting with alphanumeric"
            )
        if key in specs:
            raise ValueError(f"duplicate task key: {key}")
        title = _text(raw.get("title"), limit=500)
        if not title:
            raise ValueError(f"tasks[{index}].title is required")
        depends_on = list(dict.fromkeys(_string_list(raw.get("depends_on"), f"tasks[{index}].depends_on")))
        if key in depends_on:
            raise ValueError(f"task {key} cannot depend on itself")
        skills = list(dict.fromkeys(_string_list(raw.get("skills"), f"tasks[{index}].skills")))
        spec = dict(raw)
        spec.update(key=key, title=title, depends_on=depends_on, skills=skills)
        specs[key] = spec
        order.append(key)

    for key, spec in specs.items():
        missing = [parent for parent in spec["depends_on"] if parent not in specs]
        if missing:
            raise ValueError(f"task {key} depends on unknown task key(s): {', '.join(missing)}")

    position = {key: index for index, key in enumerate(order)}
    indegree = {key: len(specs[key]["depends_on"]) for key in order}
    children: dict[str, list[str]] = {key: [] for key in order}
    for key in order:
        for parent in specs[key]["depends_on"]:
            children[parent].append(key)
    ready = deque(key for key in order if indegree[key] == 0)
    topo: list[str] = []
    while ready:
        key = ready.popleft()
        topo.append(key)
        opened: list[str] = []
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                opened.append(child)
        for child in sorted(opened, key=position.__getitem__):
            ready.append(child)
    if len(topo) != len(order):
        cyclic = [key for key in order if indegree[key] > 0]
        raise ValueError(f"cyclic background-task dependencies: {', '.join(cyclic)}")
    return specs, topo


def _optional_int(value: Any, field: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer") from None
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def _session_id(kwargs: Mapping[str, Any]) -> str | None:
    explicit = _text(kwargs.get("session_id"), limit=500)
    if explicit:
        return explicit
    try:
        from tools.async_delegation import _current_origin_session_id

        origin = _text(_current_origin_session_id(), limit=500)
        if origin:
            return origin
    except Exception:
        pass
    return _text(os.environ.get("HERMES_SESSION_ID"), limit=500) or None


def _node_idempotency(graph_key: str, node_key: str, spec: Mapping[str, Any]) -> str | None:
    explicit = _text(spec.get("idempotency_key"), limit=500)
    if explicit:
        return explicit
    return f"assistant-graph:{graph_key}:{node_key}" if graph_key else None


def start_graph(
    args: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    *,
    default_assignee: str,
    created_by: str,
) -> dict[str, Any]:
    """Create an arbitrary acyclic durable task graph in one kernel transaction."""
    raw_tasks, final_key = _with_final_report(args.get("tasks"), args.get("final_report"))
    specs, topo = _normalize_tasks(raw_tasks)
    graph_key = _text(args.get("idempotency_key"), limit=300)
    default_priority = _optional_int(args.get("priority"), "priority") or 0
    default_project = args.get("project") if "project" in args else None
    session_id = _session_id(kwargs)

    from tools.kanban_tools import _board, _maybe_auto_subscribe

    ids: dict[str, str] = {}
    with _board(None) as (kb, conn):
        with kb.write_txn(conn):
            for key in topo:
                spec = specs[key]
                assignee = _text(spec.get("agent"), limit=300) or default_assignee
                project = spec["project"] if "project" in spec else default_project
                workspace_kind = spec.get("workspace_kind")
                workspace_path = spec.get("workspace_path")
                priority = _optional_int(spec.get("priority"), f"task {key} priority")
                max_runtime = _optional_int(
                    spec.get("max_runtime_seconds"), f"task {key} max_runtime_seconds", minimum=1
                )
                goal_max_turns = _optional_int(
                    spec.get("goal_max_turns"), f"task {key} goal_max_turns", minimum=1
                )
                ids[key] = kb.create_task(
                    conn,
                    title=spec["title"],
                    body=_text(spec.get("body")) or None,
                    assignee=assignee,
                    created_by=created_by,
                    parents=tuple(ids[parent] for parent in spec["depends_on"]),
                    priority=default_priority if priority is None else priority,
                    project_id=project,
                    workspace_kind=workspace_kind,
                    workspace_path=workspace_path,
                    idempotency_key=_node_idempotency(graph_key, key, spec),
                    max_runtime_seconds=max_runtime,
                    skills=spec["skills"] or None,
                    goal_mode=bool(spec.get("goal_mode", False)),
                    goal_max_turns=goal_max_turns,
                    session_id=session_id,
                    completion_contract=spec.get("completion_contract") or args.get("completion_contract"),
                )

        subscriptions: dict[str, bool] = {}
        nodes: list[dict[str, Any]] = []
        for key in topo:
            task_id = ids[key]
            subscriptions[key] = bool(_maybe_auto_subscribe(conn, task_id))
            task = kb.get_task(conn, task_id)
            kernel_status = task.status if task else ""
            nodes.append({
                "key": key,
                "task_id": task_id,
                "title": specs[key]["title"],
                "state": project_background_state(
                    kernel_status,
                    dependencies=specs[key]["depends_on"],
                ),
                "depends_on": list(specs[key]["depends_on"]),
            })

    subscribed_count = sum(1 for value in subscriptions.values() if value)
    notification_mode = (
        "automatic" if subscribed_count == len(nodes)
        else "partial" if subscribed_count
        else "manual"
    )
    result: dict[str, Any] = {
        "ok": True,
        "kind": "background_task_graph",
        "count": len(nodes),
        "tasks": nodes,
        "notification_mode": notification_mode,
        "subscribed_count": subscribed_count,
    }
    if final_key:
        result.update(final_task_key=final_key, final_task_id=ids[final_key])
    return result


def background_task_graph(args: dict[str, Any], **kwargs: Any) -> str:
    """Registry handler: product-level graph intent -> existing durable task kernel."""
    try:
        from tools.background_task import _current_profile_name, _default_assignee

        result = start_graph(
            args,
            kwargs,
            default_assignee=_default_assignee(),
            created_by=_current_profile_name(),
        )
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"background_task_graph: {exc}")


@no_cache_check_fn
def _check_background_task_graph_mode() -> bool:
    try:
        from tools.background_task import _check_background_task_mode

        return bool(_check_background_task_mode())
    except Exception:
        return False


_NODE_PROPERTIES = {
    "key": {
        "type": "string",
        "description": "Unique short logical key used by sibling depends_on entries.",
    },
    "title": {"type": "string", "description": "Short outcome title."},
    "body": {"type": "string", "description": "Self-contained execution specification."},
    "depends_on": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Sibling keys that must complete first. Omit/empty for parallel-ready work.",
    },
    "agent": {
        "type": "string",
        "description": "Optional specialist profile; omit for normal assistant-managed work.",
    },
    "skills": {"type": "array", "items": {"type": "string"}},
    "priority": {"type": "integer"},
    "max_runtime_seconds": {"type": "integer", "minimum": 1},
    "goal_mode": {"type": "boolean"},
    "goal_max_turns": {"type": "integer", "minimum": 1},
    "project": {"type": "string"},
    "workspace_kind": {"type": "string", "enum": ["scratch", "dir", "worktree"]},
    "workspace_path": {"type": "string"},
    "completion_contract": {"type": "string"},
    "idempotency_key": {
        "type": "string",
        "description": "Optional node-specific key for exact retry deduplication.",
    },
}


BACKGROUND_TASK_GRAPH_SCHEMA = {
    "name": "background_task_graph",
    "description": (
        "Atomically create a durable dependency graph when one user goal clearly decomposes into multiple long-running "
        "background outcomes. Use logical task keys plus depends_on: independent nodes can run in parallel, while "
        "dependent nodes wait automatically. Do not use this for ordinary questions, current-turn work, or a single "
        "background job (use background_task start). This is a high-level personal-assistant planning surface; do not "
        "call low-level task-kernel/link tools for the same plan. All nodes share the existing durable scheduler and "
        "retry kernel. When the user expects one combined completion summary, provide final_report: Stardust adds one "
        "fan-in reporting task that waits for every current leaf and receives their result handoffs. "
        "Returned task states use personal-assistant vocabulary (queued/running/waiting_*/completed/failed/cancelled). "
        "notification_mode=automatic means every node has a persisted completion/block route; partial/manual means do "
        "not promise complete automatic reporting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_GRAPH_TASKS - 1,
                "description": "Durable task nodes. depends_on references sibling logical keys, never task ids.",
                "items": {
                    "type": "object",
                    "properties": _NODE_PROPERTIES,
                    "required": ["key", "title"],
                },
            },
            "final_report": {
                "type": "object",
                "description": (
                    "Optional fan-in completion reporter. It automatically depends on every leaf task; key/title/body "
                    "default to a concise user completion report. Extra depends_on entries are additive."
                ),
                "properties": _NODE_PROPERTIES,
            },
            "project": {"type": "string", "description": "Optional default project for all nodes."},
            "priority": {"type": "integer", "description": "Optional default priority for all nodes."},
            "completion_contract": {
                "type": "string",
                "description": "Optional default completion contract for all nodes.",
            },
            "idempotency_key": {
                "type": "string",
                "description": (
                    "Optional graph retry key. Use only for retries of the exact same graph; each node derives a stable "
                    "key from this value and its logical key."
                ),
            },
        },
        "required": ["tasks"],
    },
}


registry.register(
    name="background_task_graph",
    toolset="assistant_orchestration",
    schema=BACKGROUND_TASK_GRAPH_SCHEMA,
    handler=lambda args, **kw: background_task_graph(args, **kw),
    check_fn=_check_background_task_graph_mode,
    description="Atomic parallel/dependency graph for durable personal-assistant work",
    emoji="🕸️",
)
