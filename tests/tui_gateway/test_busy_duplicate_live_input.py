"""A replay of a live prompt must not become guidance or cancel its task."""
import threading
from types import SimpleNamespace

import pytest

from agent.interrupt_control import InterruptControlMixin
from tui_gateway import server


@pytest.mark.parametrize("mode", ["interrupt", "steer", "queue"])
def test_duplicate_live_prompt_does_not_touch_agent(monkeypatch, mode):
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: mode)
    agent = InterruptControlMixin()
    agent._supports_active_turn_redirect = True
    agent._model_request_active = threading.Event()
    agent._model_request_active.set()
    agent._interrupt_requested = False
    agent._execution_thread_id = None
    session = {
        "agent": agent, "running": True, "history_lock": threading.Lock(),
        "attached_images": [], "inflight_turn": {"user": "long task", "assistant": "step one"},
    }
    result = server._handle_busy_submit("replay", "sid", session, "long task", None)
    assert result["result"]["status"] == "streaming"
    assert not agent._interrupt_requested
    assert not getattr(agent, "_pending_redirect", None)
    assert not getattr(agent, "_pending_steer", None)
    assert "corrections" not in session["inflight_turn"]
    assert not session.get("queued_prompt")


def test_same_words_from_another_author_remain_a_correction(monkeypatch):
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: "steer")
    seen = []
    session = {
        "agent": SimpleNamespace(steer=lambda text: seen.append(text) or True),
        "running": True, "history_lock": threading.Lock(), "attached_images": [],
        "inflight_turn": {"user": "long task", "assistant": "step one"},
    }
    result = server._handle_busy_submit(
        "other", "sid", session, "long task", None, turn_author={"id": "another-bot"})
    assert result["result"]["status"] == "steered"
    assert seen == ["long task"]
