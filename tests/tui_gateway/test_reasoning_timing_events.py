"""The gateway stamps the stream events the desktop times with the backend clock, and the resume
projection hands the stored span back unchanged."""

import time

from agent.reasoning_timing import event_stamped
from tui_gateway import server


def _payload(frame: dict) -> dict:
    return frame["params"]["payload"]


def test_reasoning_delta_carries_the_stamp_its_agent_callback_was_fired_with():
    with event_stamped(10.25):
        frame = server._event_frame("reasoning.delta", "sid", {"text": "thinking"})

    assert _payload(frame) == {"text": "thinking", "timestamp": 10.25}


def test_message_delta_carries_the_stamp_of_the_answer_text_it_delivers():
    with event_stamped(22.5):
        frame = server._event_frame("message.delta", "sid", {"text": "answer"})

    assert _payload(frame)["timestamp"] == 22.5


def test_timed_event_emitted_outside_an_agent_callback_is_stamped_when_it_is_emitted():
    before = time.time()
    frame = server._event_frame("message.complete", "sid", {"text": "done"})
    after = time.time()

    assert before <= _payload(frame)["timestamp"] <= after


def test_untimed_events_keep_their_payload_exactly():
    with event_stamped(10.25):
        frame = server._event_frame("thinking.delta", "sid", {"text": "spinner"})

    assert _payload(frame) == {"text": "spinner"}


def test_stored_span_reaches_the_resumed_transcript_unchanged():
    history = [{
        "role": "assistant",
        "content": "answer",
        "reasoning": "thinking",
        "timestamp": 50.0,
        "display_metadata": {"reasoning_timing": {"started_at": 10.25, "completed_at": 22.5}},
    }]

    [message] = server._history_to_messages(history)

    assert message["display_metadata"] == {"reasoning_timing": {"started_at": 10.25, "completed_at": 22.5}}
