"""Reasoning-block timing for the desktop timeline, read from one clock.

The agent stamps each stream event it fires with ``time.time()``: the first reasoning delta, the first
answer delta and the first tool call of a response. The gateway stamps the events the desktop times
with the stamp in force when it emits them (``current_event_stamp``), and ``build_assistant_message``
stores the block's span on its assistant row. A reloaded block therefore reports the seconds the live
block showed, and no renderer clock is involved.

The span is display metadata only. ``turn_context`` strips ``display_metadata`` from every model-facing
copy, so nothing here reaches the provider payload.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Iterator

#: Key under ``display_metadata`` on an assistant row that holds the block's span.
REASONING_TIMING_KEY = "reasoning_timing"

_clock = time.time
_event_stamp = threading.local()


def current_event_stamp() -> float | None:
    """The stamp of the stream event being emitted on this thread, or None outside an agent callback."""
    return getattr(_event_stamp, "value", None)


@contextlib.contextmanager
def event_stamped(stamp: float) -> Iterator[None]:
    """Hold ``stamp`` as the event stamp while a stream callback emits its event."""
    previous = getattr(_event_stamp, "value", None)
    _event_stamp.value = stamp
    try:
        yield
    finally:
        _event_stamp.value = previous


class ResponseTiming:
    """Timing of one API attempt's response. A retried attempt gets a fresh record.

    The reasoning block runs from its first delta to the first answer delta. A response that streams no
    answer text (tool calls only) closes the block when the response finishes, and its first tool event
    carries that same stamp, so the live close and the stored close are one number.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reasoning_started_at: float | None = None
        self._answer_started = False
        self._closed_at: float | None = None
        self._finished = False
        self._tool_event_claimed = False

    def reasoning_delta(self) -> float:
        now = _clock()
        with self._lock:
            if self._reasoning_started_at is None and not self._answer_started:
                self._reasoning_started_at = now
        return now

    def answer_delta(self) -> float:
        now = _clock()
        with self._lock:
            if not self._answer_started:
                self._answer_started = True
                self._closed_at = now
        return now

    def tool_event(self) -> float:
        now = _clock()
        with self._lock:
            if (
                self._finished
                and self._reasoning_started_at is not None
                and not self._answer_started
                and not self._tool_event_claimed
            ):
                self._tool_event_claimed = True
                return self._closed_at if self._closed_at is not None else now
        return now

    def finish(self) -> dict[str, float] | None:
        """Close the response at its end; return the stored span, or None when no reasoning streamed."""
        now = _clock()
        with self._lock:
            if not self._finished:
                self._finished = True
                if self._closed_at is None:
                    self._closed_at = now
            if self._reasoning_started_at is None or self._closed_at is None:
                return None
            return {"started_at": self._reasoning_started_at, "completed_at": self._closed_at}


def begin_response_timing(agent: Any) -> None:
    """Start the timing record for the API attempt about to stream."""
    agent._response_timing = ResponseTiming()


def reasoning_event_stamp(agent: Any) -> float:
    timing = getattr(agent, "_response_timing", None)
    return _clock() if timing is None else timing.reasoning_delta()


def answer_event_stamp(agent: Any) -> float:
    timing = getattr(agent, "_response_timing", None)
    return _clock() if timing is None else timing.answer_delta()


def tool_event_stamp(agent: Any) -> float:
    timing = getattr(agent, "_response_timing", None)
    return _clock() if timing is None else timing.tool_event()


def finish_response_timing(agent: Any) -> dict[str, float] | None:
    timing = getattr(agent, "_response_timing", None)
    return None if timing is None else timing.finish()
