"""Read-only views over the durable async delegation ledger.

The lifecycle module's public list performs crash recovery before reading. Task Center polling must
observe abandoned work without changing its state or delivery timing, so this module opens the
existing profile database read-only and classifies owner loss in memory.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home, hermes_home_key
from hermes_state_holders import read_only_db_uri


_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_ROWS = 50
_ACTIVE_STATES = frozenset({"running", "stalling", "finalizing"})


def _db_path():
    return get_hermes_home() / "state.db"


def _owner_is_live(pid: Any, started_at: Any) -> bool:
    try:
        process_id = int(pid or 0)
        if process_id <= 0:
            return False
        from gateway.status import _pid_exists, get_process_start_time

        if not _pid_exists(process_id):
            return False
        if started_at is None:
            return True
        return get_process_start_time(process_id) == int(started_at)
    except Exception:
        # If process identity cannot be proved, keep the task live rather than inventing a crash.
        return True


def _payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def peek_durable_delegations(*, limit: int = _MAX_ROWS) -> list[dict[str, Any]]:
    """Read recent delegation rows without recovery writes or schema initialization.

    A missing state database or table is an empty view. Other I/O and SQLite errors propagate so
    the caller can mark the delegation rail degraded.
    """
    path = _db_path()
    if not path.is_file():
        return []

    from hermes_state_dbfile import _connect_tracked_db

    conn = _connect_tracked_db(read_only_db_uri(path), tracking_path=path, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='async_delegations'"
        ).fetchone()
        if table is None:
            return []
        cutoff = time.time() - _RETENTION_SECONDS
        rows = conn.execute(
            """SELECT delegation_id, parent_session_id, state, dispatched_at, completed_at, updated_at,
                      owner_pid, owner_started_at, task_json, result_json, event_json
               FROM async_delegations
               WHERE state IN ('running', 'stalling', 'finalizing')
                  OR COALESCE(completed_at, updated_at, dispatched_at) >= ?
               ORDER BY COALESCE(completed_at, updated_at, dispatched_at) DESC, delegation_id DESC
               LIMIT ?""",
            (cutoff, max(1, min(int(limit), _MAX_ROWS))),
        ).fetchall()
    finally:
        conn.close()

    expected_owner = hermes_home_key()
    expected_path = str(_db_path().parent.resolve()).casefold()
    records: list[dict[str, Any]] = []
    for row in rows:
        task = _payload(row["task_json"])
        event = _payload(row["event_json"])
        if not task:
            task = {
                "goal": event.get("goal"),
                "goals": event.get("goals"),
                "context": event.get("context"),
            }
        parent_session_id = row["parent_session_id"]
        if parent_session_id and not event.get("parent_session_id"):
            event["parent_session_id"] = parent_session_id
        record_owner = task.get("owner_home") or event.get("owner_home")
        if record_owner not in (None, ""):
            owner_text = str(record_owner)
            try:
                owner_matches = str(Path(owner_text).resolve()).casefold() == expected_path
            except (OSError, RuntimeError, ValueError):
                owner_matches = owner_text.casefold() == expected_owner.casefold()
            if not owner_matches:
                continue
        result = _payload(row["result_json"])
        state = str(row["state"] or "")
        if state in _ACTIVE_STATES and not _owner_is_live(row["owner_pid"], row["owner_started_at"]):
            state = "unknown"
        records.append(
            {
                "delegation_id": str(row["delegation_id"]),
                "parent_session_id": row["parent_session_id"],
                "status": state,
                "dispatched_at": row["dispatched_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
                "task": task,
                "event": event,
                "result": result,
            }
        )
    return records
