"""Reasoning-block timing: where a block starts and ends, and which stamp each stream event carries.

Behavior contracts, not snapshots: the stored span must be exactly the span the live events describe,
because a reloaded block has to report the same seconds the live block showed.
"""

from types import SimpleNamespace

import pytest

from agent import reasoning_timing
from agent.reasoning_timing import (
    REASONING_TIMING_KEY,
    ResponseTiming,
    answer_event_stamp,
    begin_response_timing,
    current_event_stamp,
    event_stamped,
    reasoning_event_stamp,
    tool_event_stamp,
)
from run_agent import AIAgent


class FakeClock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 100.0
        monkeypatch.setattr(reasoning_timing, "_clock", lambda: self.now)

    def at(self, seconds: float) -> None:
        self.now = seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    return FakeClock(monkeypatch)


def _agent() -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.reasoning_callback = None
    agent.stream_delta_callback = None
    agent._stream_callback = None
    agent.verbose_logging = False
    return agent


def test_block_runs_from_its_first_reasoning_delta_to_its_first_answer_delta(clock: FakeClock):
    timing = ResponseTiming()

    clock.at(10.0)
    started = timing.reasoning_delta()
    clock.at(11.0)
    timing.reasoning_delta()
    clock.at(12.5)
    answered = timing.answer_delta()
    clock.at(13.0)
    timing.answer_delta()
    clock.at(14.0)

    assert started == 10.0
    assert answered == 12.5
    assert timing.finish() == {"started_at": 10.0, "completed_at": 12.5}


def test_tool_only_response_closes_the_block_at_response_end_and_its_first_tool_event_carries_that_stamp(
    clock: FakeClock,
):
    timing = ResponseTiming()

    clock.at(10.0)
    timing.reasoning_delta()
    clock.at(12.0)
    span = timing.finish()

    # Dispatch happens after the response finished; only the first tool event adopts the response end.
    clock.at(12.2)
    first_tool = timing.tool_event()
    clock.at(12.4)
    second_tool = timing.tool_event()

    assert span == {"started_at": 10.0, "completed_at": 12.0}
    assert first_tool == 12.0
    assert second_tool == 12.4


def test_response_without_reasoning_stores_no_span_and_tool_events_keep_their_own_stamp(clock: FakeClock):
    timing = ResponseTiming()

    clock.at(10.0)
    timing.answer_delta()
    clock.at(11.0)

    assert timing.finish() is None
    clock.at(11.5)
    assert timing.tool_event() == 11.5


def test_reasoning_that_arrives_after_answer_text_is_not_part_of_the_stored_block(clock: FakeClock):
    timing = ResponseTiming()

    clock.at(10.0)
    timing.answer_delta()
    clock.at(11.0)
    timing.reasoning_delta()
    clock.at(12.0)

    assert timing.finish() is None


def test_event_stamp_is_held_only_while_the_callback_that_emits_the_event_runs():
    assert current_event_stamp() is None

    with event_stamped(42.5):
        assert current_event_stamp() == 42.5

    assert current_event_stamp() is None


def test_agent_stamps_come_from_the_response_timing_of_the_current_attempt(clock: FakeClock):
    agent = _agent()
    begin_response_timing(agent)

    clock.at(10.0)
    assert reasoning_event_stamp(agent) == 10.0
    clock.at(12.0)
    assert answer_event_stamp(agent) == 12.0
    clock.at(12.5)
    assert tool_event_stamp(agent) == 12.5

    begin_response_timing(agent)
    clock.at(20.0)
    assert reasoning_event_stamp(agent) == 20.0


def test_agent_without_a_timing_record_still_stamps_events_with_the_clock(clock: FakeClock):
    agent = _agent()

    clock.at(33.0)

    assert reasoning_event_stamp(agent) == 33.0
    assert answer_event_stamp(agent) == 33.0
    assert tool_event_stamp(agent) == 33.0


def test_built_assistant_message_carries_the_block_span_in_display_metadata(clock: FakeClock):
    agent = _agent()
    begin_response_timing(agent)

    clock.at(10.25)
    reasoning_event_stamp(agent)
    clock.at(22.5)
    answer_event_stamp(agent)

    message = agent._build_assistant_message(
        SimpleNamespace(content="answer", reasoning="thinking", tool_calls=None), "stop"
    )

    assert message["display_metadata"] == {REASONING_TIMING_KEY: {"started_at": 10.25, "completed_at": 22.5}}


def test_built_assistant_message_without_streamed_reasoning_has_no_display_metadata(clock: FakeClock):
    agent = _agent()
    begin_response_timing(agent)

    clock.at(10.0)
    answer_event_stamp(agent)
    message = agent._build_assistant_message(SimpleNamespace(content="answer", tool_calls=None), "stop")

    assert "display_metadata" not in message
