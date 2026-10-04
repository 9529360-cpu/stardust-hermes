"""Bounded observations of an authorized child's existing operational transcript."""

from __future__ import annotations

from typing import Any

TAIL_BYTES = 16384


def read_child_activity(record: dict[str, Any] | None) -> dict[str, Any]:
    """Callers authorize the record; paths are never accepted from the requester.

    The writer already redacts this operational log. Reading it does not touch
    either conversation or its cached prompt prefix.
    """
    result = {"available": False, "text": "", "truncated": False}
    path = getattr(record.get("agent"), "_live_transcript_path", None) if record else None
    if not path:
        return result
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - TAIL_BYTES))
            text = stream.read(TAIL_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        # Starting/finishing children can create or retire the log concurrently.
        return result
    return {"available": True, "text": text, "truncated": size > TAIL_BYTES}
