"""A streamed turn's stored reasoning span is the span its live events carried, read back from the session store."""

from types import SimpleNamespace

import pytest

from agent.reasoning_timing import begin_response_timing, event_stamped, tool_event_stamp
from agent.session_persistence import _db_flush_row
from hermes_state import SessionDB
from run_agent import AIAgent
from tui_gateway import server


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return SessionDB(db_path=tmp_path / "state.db")


def test_stored_span_is_the_span_the_live_events_were_stamped_with(db):
    live_frames: list[dict] = []
    agent = object.__new__(AIAgent)
    agent.verbose_logging = False
    agent._stream_callback = None
    agent.reasoning_callback = lambda text: live_frames.append(
        server._event_frame("reasoning.delta", "sid", {"text": text})
    )
    agent.stream_delta_callback = lambda text: live_frames.append(
        server._event_frame("message.delta", "sid", {"text": text})
    )
    key = db.create_session("reasoning-timing", "test")

    begin_response_timing(agent)
    agent._fire_reasoning_delta("first thoughts")
    agent._fire_reasoning_delta(" and more")
    agent._fire_stream_delta("the answer")
    message = agent._build_assistant_message(
        SimpleNamespace(content="the answer", reasoning="first thoughts and more", tool_calls=None), "stop"
    )

    db.append_messages_batch(session_id=key, messages=[_db_flush_row(agent, message, False)])

    reasoning_frame, _, answer_frame = live_frames
    stored = db.get_messages_as_conversation(key)[-1]["display_metadata"]["reasoning_timing"]
    assert stored == {
        "started_at": reasoning_frame["params"]["payload"]["timestamp"],
        "completed_at": answer_frame["params"]["payload"]["timestamp"],
    }


def test_tool_only_response_stores_the_stamp_its_first_tool_event_carries(db):
    live_frames: list[dict] = []
    agent = object.__new__(AIAgent)
    agent.verbose_logging = False
    agent._stream_callback = None
    agent.stream_delta_callback = lambda text: None  # streaming turn: the desktop's answer sink is attached
    agent.reasoning_callback = lambda text: live_frames.append(
        server._event_frame("reasoning.delta", "sid", {"text": text})
    )
    key = db.create_session("reasoning-timing-tools", "test")

    begin_response_timing(agent)
    agent._fire_reasoning_delta("looking the file up")
    message = agent._build_assistant_message(
        SimpleNamespace(content="", reasoning="looking the file up", tool_calls=None), "tool_calls"
    )
    # Dispatch happens after the response finished; the first tool event still closes the block at the
    # response end, which is the stamp the store holds.
    with event_stamped(tool_event_stamp(agent)):
        tool_frame = server._event_frame("tool.start", "sid", {"tool_id": "call-1", "name": "read_file"})

    db.append_messages_batch(session_id=key, messages=[_db_flush_row(agent, message, False)])

    stored = db.get_messages_as_conversation(key)[-1]["display_metadata"]["reasoning_timing"]
    assert stored["completed_at"] == tool_frame["params"]["payload"]["timestamp"]
    assert stored["started_at"] == live_frames[0]["params"]["payload"]["timestamp"]
