"""A verify-on-stop held-back final answer reaches the UI flagged ``final_candidate``.

Live incident (2026-10-21): the desktop folded that already shown answer into a collapsed
"work progress" disclosure once the gate continued the turn, so the reply seemed to vanish."""

from unittest.mock import patch

import pytest

from run_agent import AIAgent


def _agent(cb):
    agent = AIAgent.__new__(AIAgent)
    agent.interim_assistant_callback = cb
    agent._current_streamed_assistant_text = ""
    agent._delivered_interim_texts = set()
    agent.show_commentary = True
    agent._enqueue_stream_hook = lambda *a, **k: None
    return agent


@pytest.mark.parametrize("reason,expected", [("verification_required", True), ("verify_hook_continue", True),
                                             ("tool_calls", False), (None, False)])
def test_final_candidate_flag_follows_stop_gate_finish_reason(reason, expected):
    calls = []
    agent = _agent(lambda text, *, already_streamed=False, final_candidate=False: calls.append(final_candidate))
    agent._emit_interim_assistant_message({"role": "assistant", "content": f"answer {reason}", "finish_reason": reason})
    assert calls == [expected]


def test_legacy_two_argument_callbacks_still_receive_the_text():
    calls = []
    agent = _agent(lambda text, *, already_streamed=False: calls.append(text))
    agent._emit_interim_assistant_message(
        {"role": "assistant", "content": "held back", "finish_reason": "verification_required"})
    assert calls == ["held back"]


def test_tui_callback_emits_final_candidate_only_when_set():
    from tui_gateway.server import _agent_cbs

    emitted = []
    with patch("tui_gateway.server._load_cfg", return_value={}), \
         patch("tui_gateway.server._emit", side_effect=lambda t, sid, p=None: emitted.append(p)):
        cb = _agent_cbs("s")["interim_assistant_callback"]
        cb("a", already_streamed=True, final_candidate=True)
        cb("b", already_streamed=True)
    assert emitted[0]["final_candidate"] is True
    assert "final_candidate" not in emitted[1]
