"""Atomic task-graph creation for the personal-assistant background-task facade.

The public surface uses logical task keys and ``depends_on`` keys. This module translates those into
one transaction in the existing Kanban kernel; it owns no scheduler, queue, or worker state itself.
Independent nodes land ``ready`` together, dependency-gated nodes land ``todo`` until their parents
complete, and every created node is subscribed to the originating assistant session when possible.
"""
from __future__ import annotations

import os
import re
from collections import deque
from typing import Any, Mapping


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_GRAPH_TASKS = 32


def _text(value: Any, *, limit: int = 10000) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _normalize_tasks(raw_tasks: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("background_task start_graph requires a non-empty tasks list")
    if len(raw_tasks) > _MAX_GRAPH_TASKS:
        raise ValueError(f"background_task start_graph supports at most {_MAX_GRAPH_TASKS} tasks")

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

    # Stable Kahn order: preserves caller order among simultaneously-ready nodes.
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
    specs, topo = _normalize_tasks(args.get("tasks"))
    graph_key = _text(args.get("idempotency_key"), limit=300)
    default_priority = _optional_int(args.get("priority"), "priority") or 0
    default_project = args.get("project") if "project" in args else None
    session_id = _session_id(kwargs)

    from tools.kanban_tools import _board, _maybe_auto_subscribe

    ids: dict[str, str] = {}
    with _board(None) as (kb, conn):
        # create_task explicitly supports nested writes so this outer transaction is the visibility
        # boundary: the dispatcher sees the whole graph or none of it.
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
            nodes.append({
                "key": key,
                "task_id": task_id,
                "title": specs[key]["title"],
                "status": task.status if task else None,
                "depends_on": list(specs[key]["depends_on"]),
            })

    subscribed_count = sum(1 for value in subscriptions.values() if value)
    notification_mode = (
        "automatic" if subscribed_count == len(nodes)
        else "partial" if subscribed_count
        else "manual"
    )
    return {
        "ok": True,
        "kind": "background_task_graph",
        "count": len(nodes),
        "tasks": nodes,
        "notification_mode": notification_mode,
        "subscribed_count": subscribed_count,
    }
