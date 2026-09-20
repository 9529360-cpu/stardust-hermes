"""Shared capture rule for returning locally-created cron work to a persisted conversation.

This module owns no state. It only snapshots the durable Desktop/TUI session identity already
bound in gateway session context so every cron creation surface applies the same delivery rule.
"""

from __future__ import annotations

from typing import Dict, Optional


def local_session_origin(
    deliver: Optional[str], source: Optional[str], session_id: Optional[str],
) -> Optional[Dict[str, str]]:
    """Normalize a validated durable local-conversation route.

    Only implicit/origin delivery may return to a conversation. Explicit local remains
    save-only and explicit external targets own their delivery.
    """
    requested = str(deliver or "").strip().lower()
    normalized_source = str(source or "").strip().lower()
    durable_session_id = str(session_id or "").strip()
    if requested not in {"", "origin"}:
        return None
    if normalized_source not in {"desktop", "tui"} or not durable_session_id:
        return None
    return {"source": normalized_source, "session_id": durable_session_id}


def capture_local_session_origin(
    deliver: Optional[str], session_id: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Capture the current Desktop/TUI conversation after delegated-child filtering."""
    try:
        from agent.delegation_context import is_delegated_child_context
        if is_delegated_child_context():
            return None
    except Exception:
        # A missing/broken delegation-context check must never widen the return scope.
        # If we cannot prove this is a top-level human session, fail closed.
        return None
    try:
        from gateway.session_context import get_session_env
        source = get_session_env("HERMES_SESSION_SOURCE", "")
        durable_session_id = get_session_env("HERMES_SESSION_ID", "") or session_id
    except Exception:
        return None
    return local_session_origin(deliver, source, durable_session_id)
