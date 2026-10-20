"""Read-only projection inputs for persisted standing goals."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hermes_constants import get_hermes_home
from hermes_state_holders import read_only_db_uri


def _goal_is_waiting(session_id: str, state: dict[str, Any], now: float) -> bool:
    waiting_since = _number(state.get("waiting_since"))
    waiting_until = _number(state.get("waiting_until"))
    waiting_on_delegations = _integer(state.get("waiting_on_delegations"))
    if waiting_until > 0:
        if now >= waiting_until:
            return False
        # The task center is a pure persisted read; it cannot consult process-local delegation
        # records because that would make one profile's live registry visible in another profile.
        return True

    try:
        if state.get("waiting_on_session"):
            session_id = str(state["waiting_on_session"])
            from tools.async_delegation_view import peek_durable_delegations

            active = {
                str(row.get("parent_session_id") or "")
                for row in peek_durable_delegations(limit=50)
                if row.get("status") in {"running", "stalling", "finalizing"}
            }
            waiting = session_id in active
        elif state.get("waiting_on_pid"):
            pid = int(state["waiting_on_pid"])
            from gateway.status import _pid_exists

            waiting = bool(pid > 0 and _pid_exists(pid))
        else:
            waiting = False
        return bool(waiting and (not waiting_since or now - waiting_since <= 24 * 60 * 60))
    except Exception:
        return False


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def list_goal_states(*, limit: int = 50) -> list[dict[str, Any]]:
    """Read and decode persisted ``goal:<session_id>`` values without opening SessionDB.

    A missing state database or ``state_meta`` table is an empty view. Malformed individual goal
    records are omitted; store-level failures propagate so the RPC can report a degraded rail.
    """
    path = get_hermes_home() / "state.db"
    if not path.is_file():
        return []

    from hermes_state_dbfile import _connect_tracked_db

    conn = _connect_tracked_db(read_only_db_uri(path), tracking_path=path, uri=True, timeout=1.0)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_meta'"
        ).fetchone()
        if table is None:
            return []
        rows = conn.execute(
            "SELECT substr(key, 6) AS session_id, value FROM state_meta "
            "WHERE substr(key, 1, 5) = 'goal:' ORDER BY key LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()
    finally:
        conn.close()

    now = time.time()
    goals: list[dict[str, Any]] = []
    for session_id, raw in rows:
        if not session_id or not isinstance(raw, str):
            continue
        try:
            state = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(state, dict) or state.get("status") == "cleared":
            continue
        goals.append(
            {
                "session_id": str(session_id),
                "goal": state,
                "waiting": _goal_is_waiting(str(session_id), state, now),
            }
        )
    return goals
