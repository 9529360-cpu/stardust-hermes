"""Todo tool: revisioned task state for multi-step work.

Each live AIAgent owns an in-memory ``TodoStore`` for fast turn-local access; successful
writes are mirrored into session state so fresh gateway agents and compression children can
restore the same revision without depending on old transcript rows. The human-readable list
is still re-injected after context compression. One ``todo_list`` tool: pass ``todos`` to
write, omit to read; every call returns the full list. No system-prompt mutation."""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
TODO_SESSION_STATE_KEY = "_todo_state"
# The list is re-read after every compression (format_for_injection), so unbounded
# content/count would defeat the compression it rides through. Caps apply equally to
# model-authored items and caller-replayed API history.
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
MAX_TODO_DEPENDENCIES = 16
# Max single todo tool-result payload accepted during history hydration, so a forged
# oversized result is dropped before parsing (AIAgent._hydrate_todo_store).
MAX_TODO_RESULT_CHARS = 512_000
_TRUNCATION_MARKER = "… [truncated]"
# Persisted as ordinary message content; ContextCompressor keys on this stable header to
# tell the synthetic post-compaction row from a real user message.
TODO_INJECTION_HEADER = "[Your active task list was preserved across context compression]"
_STATUS_MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
_ACTIVE_STATUSES = {"pending", "in_progress"}


class TodoStore:
    """In-memory todo list, one per AIAgent. List position is priority; items are
    ``{id, content, status, parent?, depends_on?}``; ``parent`` nests a subtask, while each
    ``depends_on`` id must be completed before the item can start."""

    def __init__(self):
        self._items: List[Dict[str, Any]] = []
        self._revision = 0

    def _fresh_items(self, todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and dedupe a whole new list (replace / restore)."""
        items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        return items

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, Any]]:
        """Replace the list (default) or merge by id; returns the full list after writing."""
        before = self.read()
        if merge:
            self._merge(todos)
        else:
            self._items = self._fresh_items(todos)
        del self._items[MAX_TODO_ITEMS:]  # keep the priority head; replays can't grow unbounded
        self._normalize_items()
        if self._items != before:
            self._revision += 1
        return self.read()

    def _merge(self, todos: List[Dict[str, Any]]) -> None:
        """Update existing items only in the fields provided; append new ones (validated)."""
        existing = {item["id"]: item for item in self._items}
        for t in self._dedupe_by_id(todos):
            item_id = str(t.get("id", "")).strip()
            if not item_id:
                continue  # can't merge without an id
            cur = existing.get(item_id)
            if cur is None:
                validated = self._validate(t)
                existing[validated["id"]] = validated
                self._items.append(validated)
                continue
            if t.get("content"):
                cur["content"] = self._cap_content(str(t["content"]).strip())
            if t.get("status") and str(t["status"]).strip().lower() in VALID_STATUSES:
                cur["status"] = str(t["status"]).strip().lower()
            if "parent" in t:
                parent = str(t["parent"] or "").strip()
                if parent:
                    cur["parent"] = parent
                else:
                    cur.pop("parent", None)
            if isinstance(t.get("depends_on"), list):
                dependencies = self._normalize_dependencies(t.get("depends_on"))
                if dependencies:
                    cur["depends_on"] = dependencies
                else:
                    cur.pop("depends_on", None)
        # Rebuild preserving original order for existing items (first occurrence wins).
        rebuilt = {item["id"]: existing.get(item["id"], item) for item in self._items}
        self._items = list(rebuilt.values())

    def read(self) -> List[Dict[str, Any]]:
        return [
            {**item, **({"depends_on": item["depends_on"].copy()} if "depends_on" in item else {})}
            for item in self._items
        ]

    def has_items(self) -> bool:
        return bool(self._items)

    def snapshot(self) -> Dict[str, Any]:
        """Full state clients can reconcile atomically."""
        return {"todos": self.read(), "revision": self._revision}

    def restore(self, todos: List[Dict[str, Any]], *, revision: Any = 0) -> List[Dict[str, Any]]:
        """Restore a trusted snapshot without manufacturing a new revision."""
        self._items = self._fresh_items(todos)[:MAX_TODO_ITEMS]
        self._normalize_items()
        try:
            self._revision = max(0, int(revision or 0))
        except (TypeError, ValueError):
            self._revision = 0
        return self.read()

    def format_for_injection(self) -> Optional[str]:
        """Render the list for post-compression injection, or None if nothing active. Only
        pending/in_progress items are injected — finished ones make the model re-do work after
        compression. A parent is kept (with its real status marker) when any descendant is
        active so subtasks keep context."""
        if not self._items:
            return None
        children: Dict[str, List[Dict[str, Any]]] = {}
        for item in self._items:
            if item.get("parent"):
                children.setdefault(item["parent"], []).append(item)

        def render(item: Dict[str, Any], depth: int, out: List[str]) -> bool:
            kid_lines: List[str] = []
            has_active_kid = False
            for kid in children.get(item["id"], []):
                has_active_kid |= render(kid, depth + 1, kid_lines)
            keep = item["status"] in _ACTIVE_STATUSES or has_active_kid
            if keep:
                marker = _STATUS_MARKERS.get(item["status"], "[?]")
                line = (f"{'  ' * depth}- {marker} {item['id']}. "
                        f"{item['content']} ({item['status']})")
                blockers = self.dependency_blockers(item, self._items)
                if blockers:
                    line += f" [waiting for: {', '.join(blockers)}]"
                out.append(line)
                out.extend(kid_lines)
            return keep

        lines = [TODO_INJECTION_HEADER]
        for item in self._items:
            if not item.get("parent"):
                render(item, 0, lines)
        return "\n".join(lines) if len(lines) > 1 else None

    @staticmethod
    def _cap_content(content: str) -> str:
        """Truncate to MAX_TODO_CONTENT_CHARS keeping the head (the actionable part) + marker."""
        if len(content) > MAX_TODO_CONTENT_CHARS:
            return content[:MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize one item and preserve optional hierarchy and prerequisite relationships."""
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip().lower()
        result = {"id": item_id,
                  "content": TodoStore._cap_content(content) if content else "(no description)",
                  "status": status if status in VALID_STATUSES else "pending"}
        parent = str(item.get("parent") or "").strip()
        if parent and parent != item_id:
            result["parent"] = parent
        dependencies = TodoStore._normalize_dependencies(item.get("depends_on"))
        if dependencies:
            result["depends_on"] = dependencies
        return result

    @staticmethod
    def _normalize_dependencies(value: Any) -> List[str]:
        """Keep a bounded, ordered list of prerequisite task ids."""
        if not isinstance(value, list):
            return []
        result: List[str] = []
        for raw_id in value[:MAX_TODO_DEPENDENCIES]:
            dependency_id = str(raw_id or "").strip()
            if dependency_id and dependency_id not in result:
                result.append(dependency_id)
        return result

    @staticmethod
    def dependency_blockers(item: Dict[str, Any], items: List[Dict[str, Any]]) -> List[str]:
        """Return missing or unfinished prerequisites in their authored order."""
        dependencies = item.get("depends_on")
        if not isinstance(dependencies, list) or not dependencies:
            return []
        status_by_id = {candidate["id"]: candidate["status"] for candidate in items}
        return [dependency_id for dependency_id in dependencies
                if status_by_id.get(dependency_id) != "completed"]

    def _normalize_items(self) -> None:
        """Keep hierarchy and activation state valid after writes and snapshot restores."""
        self._sanitize_parents(self._items)
        for item in self._items:
            if item["status"] == "in_progress" and self.dependency_blockers(item, self._items):
                item["status"] = "pending"
        self._items = self._normalize_order(self._items)
        self._items = self._keep_single_active(self._items)

    @staticmethod
    def _sanitize_parents(items: List[Dict[str, Any]]) -> None:
        """Drop dangling parent refs and break cycles in place (such items become roots)."""
        by_id = {item["id"]: item for item in items}
        for item in items:
            if item.get("parent") and item["parent"] not in by_id:
                item.pop("parent", None)
        for item in items:
            seen, node = {item["id"]}, item
            while node.get("parent"):
                if node["parent"] in seen:
                    item.pop("parent", None)
                    break
                seen.add(node["parent"])
                node = by_id[node["parent"]]

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):  # non-dicts get a synthetic key; _validate handles them
            key = str(item.get("id", "")).strip() if isinstance(item, dict) else f"__invalid_{i}"
            last_index[key or "?"] = i
        return [todos[i] for i in sorted(last_index.values())]

    @staticmethod
    def _normalize_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Lift the in_progress step ahead of any earlier pending placeholder. Nested lists
        keep authored order — reordering would tear a subtask from its siblings."""
        statuses = [item["status"] for item in items]
        if any(item.get("parent") for item in items) or "in_progress" not in statuses:
            return items
        active_index = statuses.index("in_progress")
        if "pending" not in statuses[:active_index]:
            return items
        normalized = items.copy()
        normalized.insert(statuses.index("pending"), normalized.pop(active_index))
        return normalized

    @staticmethod
    def _keep_single_active(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep the first active item by list priority; return conflicting items to pending."""
        active_seen = False
        for item in items:
            if item["status"] != "in_progress":
                continue
            if active_seen:
                item["status"] = "pending"
            else:
                active_seen = True
        return items


def load_todo_session_state(session_db: Any, session_id: str) -> Optional[Dict[str, Any]]:
    """Return the trusted session-scoped todo snapshot, or ``None`` when unavailable.

    The snapshot lives in ``sessions.model_config`` so fresh gateway agents do not depend on an
    old ``todo_list`` tool-result surviving transcript compaction. History replay remains a
    fallback for older sessions that predate this state slot.
    """
    if not session_db or not session_id:
        return None
    reader = getattr(session_db, "get_session_model_config_value", None)
    if not callable(reader):
        return None
    try:
        raw = reader(session_id, TODO_SESSION_STATE_KEY)
    except Exception:
        logger.debug("Could not read persisted todo state for session %s", session_id, exc_info=True)
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("todos"), list):
        return None
    try:
        revision = max(0, int(raw.get("revision", 0) or 0))
    except (TypeError, ValueError):
        return None
    return {"todos": raw["todos"], "revision": revision}


def persist_todo_session_state(session_db: Any, session_id: str, store: "TodoStore") -> bool:
    """Persist the authoritative todo snapshot without allowing revision rollback.

    Transcript tool results remain the recovery fallback. A stale/conflicting state write therefore
    returns ``False`` but never turns an otherwise successful todo update into a failed user turn.
    """
    if not session_db or not session_id or store is None:
        return False
    snapshot = store.snapshot()
    monotonic_writer = getattr(session_db, "patch_session_model_config_monotonic", None)
    legacy_writer = getattr(session_db, "patch_session_model_config", None)
    if not callable(monotonic_writer) and not callable(legacy_writer):
        return False
    try:
        if callable(monotonic_writer):
            persisted = bool(monotonic_writer(session_id, TODO_SESSION_STATE_KEY, snapshot))
            if not persisted:
                logger.info(
                    "Skipped stale or conflicting todo state for session %s at revision %s",
                    session_id, snapshot.get("revision", 0),
                )
            return persisted
        legacy_writer(session_id, {TODO_SESSION_STATE_KEY: snapshot})
    except Exception:
        logger.warning("Could not persist todo state for session %s", session_id, exc_info=True)
        return False
    return True


def todo_tool(todos: Optional[List[Dict[str, Any]]] = None, merge: bool = False,
              store: Optional[TodoStore] = None) -> str:
    """Write ``todos`` (replace, or ``merge`` by id) or read when None -> list + summary JSON."""
    if store is None:
        return tool_error("TodoStore not initialized")
    previously_active_ids = [
        item["id"] for item in store.read() if item["status"] == "in_progress"
    ]
    requested_active_ids: List[str] = []
    if todos is None:
        items = store.read()
    else:
        if isinstance(todos, str):  # LLMs sometimes send a JSON string instead of a list
            try:
                todos = json.loads(todos)
            except (json.JSONDecodeError, TypeError):
                return tool_error("todos must be a list of objects, got unparseable string")
        if not isinstance(todos, list):
            return tool_error(f"todos must be a list, got {type(todos).__name__}")
        requested_active_ids = [
            str(item.get("id", "")).strip() or "?"
            for item in todos
            if isinstance(item, dict)
            and str(item.get("status", "")).strip().lower() == "in_progress"
        ]
        items = store.write(todos, merge)
    summary = {"total": len(items)}
    for status in ("pending", "in_progress", "completed", "cancelled"):
        summary[status] = sum(1 for i in items if i["status"] == status)
    result: Dict[str, Any] = {
        "todos": items, "revision": store.snapshot()["revision"], "summary": summary,
    }
    active_ids = {item["id"] for item in items if item["status"] == "in_progress"}
    items_by_id = {item["id"]: item for item in items}
    blocked_ids = set()
    warnings = []
    for item_id in dict.fromkeys(requested_active_ids + previously_active_ids):
        item = items_by_id.get(item_id)
        blockers = TodoStore.dependency_blockers(item, items) if item else []
        if item_id not in active_ids and blockers:
            blocked_ids.add(item_id)
            warnings.append(
                f"Task {item_id} remains pending until prerequisite tasks are completed: "
                f"{', '.join(blockers)}."
            )
    if any(item_id not in active_ids and item_id not in blocked_ids for item_id in requested_active_ids):
        warnings.append(
            "Only one todo may be in_progress. The first active item by list priority remains active; "
            "conflicting activation requests are pending. Complete or cancel the active item before "
            "activating another."
        )
    if warnings:
        result["warnings"] = warnings
    return json.dumps(result, ensure_ascii=False)


def check_todo_requirements() -> bool:
    """Todo tool has no external requirements -- always available."""
    return True


# Behavioral guidance is baked into the (static, cached) description; item shape and merge
# semantics live ONLY in the parameter schema.
TODO_SCHEMA = {
    "name": "todo_list",
    "description": (
        # See #95681.
        "Track a task list for multi-step work (3+ steps). Use for complex tasks "
        "with 3+ steps or when the user provides multiple tasks. "
        "For 'all N items' tasks, enumerate every instance as its own checklist "
        "item so none are silently dropped. "
        "Call with no parameters to read the current list.\n"
        "List order is priority. Only ONE item in_progress at a time. "
        "Break large phases into subtasks via parent. "
        "Use depends_on for prerequisites; a task cannot start until every prerequisite is completed. "
        "Mark an item completed only after the work is verified done, never "
        "based on intent. If something fails, cancel it and add a revised "
        "item. Always returns the full current list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string"
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"]
                        },
                        "parent": {
                            "type": "string",
                            "description": "Optional id of another item, making this a nested subtask. Omit for top-level."
                        },
                        "depends_on": {
                            "type": "array",
                            "description": "Optional task ids that must be completed before this task can start.",
                            "items": {"type": "string"},
                            "maxItems": MAX_TODO_DEPENDENCIES
                        }
                    },
                    "required": ["id", "content", "status"]
                }
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list with a fresh plan."
                ),
                "default": False
            }
        },
        "required": []
    }
}


from tools.registry import registry, tool_error

registry.register(
    name="todo_list", toolset="todo", schema=TODO_SCHEMA, check_fn=check_todo_requirements,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False), store=kw.get("store")),
    emoji="📋")
