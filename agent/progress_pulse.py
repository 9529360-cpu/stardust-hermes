"""Progress pulse: notice when a tool-heavy turn has gone quiet and ask the model for a one-line update.

The user sees the agent's words, not its tool calls. Field case (owner's development chats, 2026-10-11..13):
35 of 81 working turns went more than three minutes without a visible line and 13 went more than ten, while
the owner asked "好了吗" / "你是不是没有在干活" mid-work. ``PROGRESS_UPDATE_GUIDANCE`` asks the model for
progress notes; this pulse is the backstop when it forgets.

The note rides the tool result being committed (the slot subdirectory hints use), so it is part of that result
from its first persisted byte and the prompt cache is untouched. It is only sent while a UI is listening for
mid-turn text (``interim_assistant_callback``): a cron run or a child agent has nobody to update.
"""

from __future__ import annotations

import time
from typing import Any

PROGRESS_CHECK_MARKER = "[Progress check]"
# A minute of tool work with no visible line is long enough to look stalled.
NUDGE_AFTER_SECONDS = 60.0


def _now() -> float:
    return time.monotonic()


def mark_user_updated(agent: Any) -> None:
    """The user just spoke, or just saw a line from the agent: the quiet clock restarts."""
    now = _now()
    agent._progress_quiet_since = now
    agent._progress_next_nudge_at = now + NUDGE_AFTER_SECONDS


def nudge_for_tool_result(agent: Any) -> str:
    """Suffix for the tool result being committed: the progress check when the turn has gone quiet, else ""."""
    if not getattr(agent, "progress_updates", False) or getattr(agent, "interim_assistant_callback", None) is None:
        return ""
    next_at = getattr(agent, "_progress_next_nudge_at", None)
    now = _now()
    if next_at is None or now < next_at:
        return ""
    # Still quiet a full window later -> ask again, never more than once a window.
    agent._progress_next_nudge_at = now + NUDGE_AFTER_SECONDS
    minutes = max(1, round((now - agent._progress_quiet_since) / 60))
    return (
        f"\n\n{PROGRESS_CHECK_MARKER} The user has seen no update from you for about {minutes} min of work. "
        "Start your next response with one short line in the user's language: what you found and what you are "
        "doing next. Then continue."
    )
