"""Shared capture rule for returning locally-created cron work to a persisted conversation.

This module owns no state. It only snapshots the durable Desktop/TUI session identity already
bound in gateway session context so every cron creation surface applies the same delivery rule.
"""

from __future__ import annotations

from typing import Dict, Optional


def capture_local_session_origin(
    deliver: Optional[str], session_id: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Return a durable local conversation route for implicit/origin delivery.

    Explicit local is save-only and explicit messaging targets own their delivery. Delegated
    children never become the destination for durable work. The persisted session id survives
    window/tab churn; ephemeral UI session ids are deliberately excluded.
    """
    requested = str(deliver or "").strip().lower()
    if requested not in {"", "origin"}:
        return None
    try:
        from agent.delegation_context import is_delegated_child_context
        if is_delegated_child_context():
            return None
    except Exception:
        pass
    try:
        from gateway.session_context import get_session_env
        source = str(get_session_env("HERMES_SESSION_SOURCE", "") or "").strip().lower()
        durable_session_id = str(
            get_session_env("HERMES_SESSION_ID", "") or session_id or ""
        ).strip()
    except Exception:
        return None
    if source not in {"desktop", "tui"} or not durable_session_id:
        return None
    return {"source": source, "session_id": durable_session_id}
