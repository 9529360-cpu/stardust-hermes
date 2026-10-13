"""A tool-heavy turn that shows the user nothing for a minute gets a progress check on its next tool result.

Field case (owner's development chats, 2026-10-11..13): 13 of 81 working turns went more than ten minutes
without a visible line while the owner asked "好了吗" / "你是不是没有在干活".
"""

import json
from types import SimpleNamespace

import pytest

from agent import progress_pulse
from agent.progress_pulse import NUDGE_AFTER_SECONDS, PROGRESS_CHECK_MARKER


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(progress_pulse, "_now", c)
    return c


def _listening_agent(**overrides):
    agent = SimpleNamespace(progress_updates=True, interim_assistant_callback=lambda *a, **k: None)
    for key, value in overrides.items():
        setattr(agent, key, value)
    progress_pulse.mark_user_updated(agent)
    return agent


def test_quiet_turn_gets_one_check_per_window(clock):
    agent = _listening_agent()

    clock.advance(NUDGE_AFTER_SECONDS - 1)
    assert progress_pulse.nudge_for_tool_result(agent) == ""

    clock.advance(1)
    first = progress_pulse.nudge_for_tool_result(agent)
    assert PROGRESS_CHECK_MARKER in first and "about 1 min" in first
    assert progress_pulse.nudge_for_tool_result(agent) == ""  # same window: once

    clock.advance(NUDGE_AFTER_SECONDS)
    assert "about 2 min" in progress_pulse.nudge_for_tool_result(agent)  # still quiet: ask again


def test_a_visible_line_restarts_the_quiet_clock(clock):
    agent = _listening_agent()
    clock.advance(NUDGE_AFTER_SECONDS - 5)

    progress_pulse.mark_user_updated(agent)
    clock.advance(10)

    assert progress_pulse.nudge_for_tool_result(agent) == ""


@pytest.mark.parametrize("overrides", [{"progress_updates": False}, {"interim_assistant_callback": None}])
def test_no_check_when_switched_off_or_nobody_is_listening(clock, overrides):
    agent = _listening_agent(**overrides)
    clock.advance(NUDGE_AFTER_SECONDS * 3)

    assert progress_pulse.nudge_for_tool_result(agent) == ""


# ── through the real turn loop ───────────────────────────────────────────────────────────────────────────


def _tool_call_response(content):
    call = SimpleNamespace(
        id="call_1", type="function", function=SimpleNamespace(name="read_file", arguments='{"path": "README.md"}'),
    )
    message = SimpleNamespace(content=content, reasoning=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None)


def _final_response():
    message = SimpleNamespace(content="done", reasoning=None, tool_calls=[])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _run_turn(monkeypatch, clock, *, first_content, model_seconds, tool_seconds):
    """One turn: a tool call (after ``model_seconds``), a tool taking ``tool_seconds``, then "done".
    Returns (messages sent with the second request, interim lines shown to the user)."""
    requests = []

    class _Completions:
        def create(self, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                clock.advance(model_seconds)
                return _tool_call_response(first_content)
            return _final_response()

    def _slow_tool(name, args, task_id=None, **kwargs):
        clock.advance(tool_seconds)
        return json.dumps({"ok": True})

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", lambda **kwargs: fake_client)
    monkeypatch.setattr(
        "model_tools.get_tool_definitions", lambda *args, **kwargs: [{"function": {"name": "read_file"}}],
    )
    monkeypatch.setattr("model_tools.handle_function_call", _slow_tool)

    from run_agent import AIAgent

    shown = []
    agent = AIAgent(
        model="test-model", api_key="test-key", base_url="http://localhost:8080/v1", platform="cli",
        max_iterations=4, quiet_mode=True, skip_memory=True,
        interim_assistant_callback=lambda text, *, already_streamed=False: shown.append(text),
    )
    agent._disable_streaming = True
    assert agent.progress_updates is True  # display.progress_updates defaults on

    result = agent.run_conversation("look at the readme")

    assert result["final_response"].startswith("done")
    second_request_messages = requests[1]["messages"]
    return second_request_messages, shown


def _tool_results(messages):
    return [m for m in messages if m.get("role") == "tool"]


def test_silent_tool_work_reaches_the_model_with_a_progress_check(monkeypatch, clock):
    messages, shown = _run_turn(monkeypatch, clock, first_content=None, model_seconds=50, tool_seconds=30)

    assert shown == []
    (tool_result,) = _tool_results(messages)
    assert PROGRESS_CHECK_MARKER in str(tool_result["content"])


def test_a_progress_line_with_the_tool_call_keeps_the_check_away(monkeypatch, clock):
    messages, shown = _run_turn(
        monkeypatch, clock, first_content="正在读 README，看入口在哪。", model_seconds=50, tool_seconds=30,
    )

    assert shown == ["正在读 README，看入口在哪。"]
    (tool_result,) = _tool_results(messages)
    assert PROGRESS_CHECK_MARKER not in str(tool_result["content"])
