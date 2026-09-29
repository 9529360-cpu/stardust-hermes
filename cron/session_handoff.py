"""Bounded creation-time conversation handoff for continuable cron jobs.

This module owns no durable state. It snapshots a small, text-only tail from the
existing SessionDB when an agent explicitly attaches a cron job to its session.
The snapshot is stored on the job so future runs survive restarts without
reading a conversation that may have drifted to unrelated topics.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("cron.scheduler")

MAX_HANDOFF_MESSAGES = 12
MAX_HANDOFF_CHARS = 6000


def _text_content(content: Any) -> str:
    """Return user-visible text without replaying tool payloads or rich metadata."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            text = part["text"].strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _render_recent_dialogue(messages: list[dict[str, Any]]) -> Optional[str]:
    """Render the newest useful user/assistant turns within fixed size bounds."""
    useful: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = _text_content(message.get("content"))
        if text:
            useful.append((role, text))
    useful = useful[-MAX_HANDOFF_MESSAGES:]
    if not useful:
        return None

    # Prefer the most recent turns when the tail is still too large. A single
    # oversized turn is clipped from its end so the beginning of the user's
    # instruction remains intact.
    selected: list[str] = []
    remaining = MAX_HANDOFF_CHARS
    for role, text in reversed(useful):
        label = "USER" if role == "user" else "ASSISTANT"
        prefix = f"{label}: "
        block = prefix + text
        separator_cost = 2 if selected else 0
        available = remaining - separator_cost
        if available <= len(prefix):
            break
        if len(block) > available:
            room = max(0, available - len(prefix) - len("\n[... truncated ...]"))
            block = prefix + text[:room].rstrip() + "\n[... truncated ...]"
        selected.append(block)
        remaining -= len(block) + separator_cost
        if remaining <= 0:
            break

    if not selected:
        return None
    return "\n\n".join(reversed(selected))


def capture_session_handoff(session_id: Optional[str]) -> Optional[str]:
    """Snapshot recent dialogue for *session_id*, best-effort and profile-local.

    The caller already owns the active profile scope. Reuse the shared SessionDB
    registry instead of opening a second writer. Failure never blocks scheduling;
    it simply leaves the job with its normal self-contained prompt.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None

    db = None
    try:
        from hermes_state_registry import acquire

        db = acquire()
        messages = db.get_messages_as_conversation(
            sid, include_ancestors=True, include_compacted=True
        )
        return _render_recent_dialogue(list(messages or []))
    except Exception:
        logger.debug("Could not capture cron session handoff for %s", sid, exc_info=True)
        return None
    finally:
        if db is not None:
            try:
                from hermes_state_registry import release_or_close

                release_or_close(db)
            except Exception:
                logger.debug("Could not release SessionDB after cron handoff capture", exc_info=True)
