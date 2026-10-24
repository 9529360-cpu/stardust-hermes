"""DL-P2.8e — opt-in evidence gate for messaging completion claims.

A "done/fixed/完成/搞定" claim in a final response on a messaging surface needs
a successful tool result from THIS turn behind it; without one the turn
continues once with a nudge. Off by default (``agent.verify_on_stop_messaging``),
byte-identical behaviour when off, and the user's explicit instruction ("不用验证")
always wins (DISCOVERY-8 u17/u18/c03).
"""

import json

import pytest

from agent.turn_stop_gates import apply_stop_gates
from agent.verification_stop import (
    contains_completion_claim,
    messaging_claim_gate_nudge,
    turn_tool_evidence,
    user_opted_out_of_verification,
    verify_on_stop_messaging_enabled,
)


FLAG_ON = {"agent": {"verify_on_stop_messaging": True}}
FLAG_OFF = {"agent": {}}


@pytest.fixture(autouse=True)
def _messaging_surface(monkeypatch, tmp_path):
    """Telegraph surface + isolated home; the claim gate is messaging-only."""
    monkeypatch.setenv("STARDUST_SESSION_PLATFORM", "telegram")
    for var in ("STARDUST_VERIFY_ON_STOP", "STARDUST_PLATFORM", "STARDUST_SESSION_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))


def _messages(tool_rows=None):
    rows = [{"role": "user", "content": "帮我把文件发出去"}]
    rows.extend(tool_rows or [])
    return rows


def _tool_row(name="terminal", content='{"success": true, "output": "ok"}', call_id="c1"):
    return {"role": "tool", "name": name, "content": content, "tool_call_id": call_id}


# ── flag reading ────────────────────────────────────────────────────────


def test_flag_defaults_off_and_reads_agent_section():
    assert verify_on_stop_messaging_enabled() is False
    assert verify_on_stop_messaging_enabled({}) is False
    assert verify_on_stop_messaging_enabled({"agent": {}}) is False
    assert verify_on_stop_messaging_enabled({"agent": {"verify_on_stop_messaging": False}}) is False
    assert verify_on_stop_messaging_enabled({"agent": {"verify_on_stop_messaging": True}}) is True
    assert verify_on_stop_messaging_enabled({"agent": {"verify_on_stop_messaging": "true"}}) is True


# ── claim lexicon ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("已修复登录跳转的bug", "已修复"),
    ("文件已发送给对方", "已发送"),
    ("搞定了", "搞定了"),
    ("改完 了", None),  # space inside: no match, conservative
    ("Done — all tests green", "Done"),
    ("The fix is complete", "complete"),
    ("I will fix it later", None),
    ("还没完成，明天继续", None),
    ("你好，请问需要什么帮助？", None),
    ("好像差不多了", None),
    ("待完成清单如下", None),
    ("好了，现在可以用了", "好了"),
    ("还没完成A，但B已删除", "已删除"),  # clause-local negation does not silence the claim
])
def test_completion_claim_lexicon(text, expected):
    claim = contains_completion_claim(text)
    if expected is None:
        assert claim is None
    else:
        assert claim is not None and expected in claim


@pytest.mark.parametrize("text,expected", [
    ("不用验证，直接收尾", True),
    ("不要再跑测试了", True),
    ("以我的指令为准", True),
    ("skip verification", True),
    ("测试已经全绿了（结果在 test_results.txt）。不要再重跑，直接基于现有结果收尾。", True),
    ("帮我修一下", False),
])
def test_user_opt_out(text, expected):
    assert user_opted_out_of_verification(text) is expected


# ── turn-tool evidence boundary ─────────────────────────────────────────


def test_turn_tool_evidence_reads_only_this_turn():
    history = [
        {"role": "user", "content": "旧的一轮"},
        {"role": "assistant", "tool_calls": [{"id": "h1", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "tool", "name": "terminal", "content": '{"success": true}', "tool_call_id": "h1"},
        {"role": "assistant", "content": "上轮已处理"},
    ]
    current = _messages([
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "terminal", "arguments": "{}"}}]},
        _tool_row(),
    ])
    messages = history + current

    pairs = turn_tool_evidence(messages, current_turn_user_idx=4)
    assert [name for name, _ in pairs] == ["terminal"]

    # Bad boundary fails closed: no evidence considered.
    assert turn_tool_evidence(messages, current_turn_user_idx=-1) == []
    assert turn_tool_evidence(messages, current_turn_user_idx=None) == []


# ── the gate: flag x surface x claim x evidence ─────────────────────────


def test_claim_with_evidence_passes():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送", messages=_messages([_tool_row()]),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
    )
    assert nudge is None


def test_claim_without_tool_call_nudges_once():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送", messages=_messages(),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
    )
    assert nudge is not None and "以用户指令为准" in nudge


def test_claim_with_tool_error_nudges():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送",
        messages=_messages([_tool_row(content='{"error": "timeout", "is_error": true}')]),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
    )
    assert nudge is not None


def test_no_claim_no_nudge():
    nudge = messaging_claim_gate_nudge(
        final_response="我这就去办", messages=_messages(),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
    )
    assert nudge is None


def test_flag_off_never_nudges():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送", messages=_messages(),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_OFF,
    )
    assert nudge is None


def test_user_opt_out_wins():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送", messages=_messages(),
        current_turn_user_idx=0, user_message="不用验证，直接收尾", config=FLAG_ON,
    )
    assert nudge is None


def test_housekeeping_tool_is_not_evidence():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送",
        messages=_messages([_tool_row(name="todo", content='{"success": true}')]),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
    )
    assert nudge is not None


def test_second_attempt_is_bounded():
    nudge = messaging_claim_gate_nudge(
        final_response="已发送", messages=_messages(),
        current_turn_user_idx=0, user_message="发出去", config=FLAG_ON, attempts=1,
    )
    assert nudge is None


def test_non_messaging_surface_unchanged():
    import agent.verification_stop as vs

    saved = vs._session_is_messaging_surface
    vs._session_is_messaging_surface = lambda: False
    try:
        nudge = messaging_claim_gate_nudge(
            final_response="已发送", messages=_messages(),
            current_turn_user_idx=0, user_message="发出去", config=FLAG_ON,
        )
        assert nudge is None
    finally:
        vs._session_is_messaging_surface = saved


# ── stop-gate integration (shared counter) ──────────────────────────────


class _GateAgent:
    """Minimal agent stand-in for apply_stop_gates (repetition-test pattern)."""

    def __init__(self, messages, user_idx):
        self.session_id = "s1"
        self._session_messages = None
        self._persist_user_message_idx = user_idx
        self._verification_stop_nudges = 0
        self._turn_file_mutation_paths = set()
        self._flush_messages_to_session_db = lambda m, h: True
        self._emit_interim_assistant_message = lambda m: None
        self._interim_content_was_streamed = lambda text: False
        self._messages = messages


def _apply(agent, text, messages):
    return apply_stop_gates(
        agent, {"role": "assistant", "content": text}, final_response=text,
        messages=messages, conversation_history=[],
        pending_verification_response=None, pending_verification_response_previewed=None,
    )


def test_stop_gate_flag_on_nudges_claim_without_evidence(monkeypatch):
    monkeypatch.setattr(
        "agent.verification_stop.verify_on_stop_messaging_enabled", lambda config=None: True
    )
    messages = _messages()
    agent = _GateAgent(messages, 0)
    verdict = _apply(agent, "已发送", messages)
    assert verdict.continue_turn is True
    assert agent._verification_stop_nudges == 1
    # The nudge row is a synthetic user row; role alternation holds.
    assert messages[-1]["role"] == "user"
    assert messages[-1].get("_verification_stop_synthetic") is True
    # attempts already 1: the gate does not fire again this turn.
    assert _apply(agent, "已发送", messages).continue_turn is False or agent._verification_stop_nudges == 1


def test_stop_gate_flag_off_byte_identical(monkeypatch):
    monkeypatch.setattr(
        "agent.verification_stop.verify_on_stop_messaging_enabled", lambda config=None: False
    )
    messages = _messages()
    agent = _GateAgent(messages, 0)
    verdict = _apply(agent, "已发送", messages)
    assert verdict.continue_turn is False
    assert agent._verification_stop_nudges == 0
    assert [m["role"] for m in messages] == ["user"]  # nothing appended


def test_stop_gate_edit_gate_takes_priority_and_shares_counter(monkeypatch, tmp_path):
    """The existing edit gate and the claim gate share one counter per turn."""
    from agent.verification_evidence import mark_workspace_edited

    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setattr(
        "agent.verification_stop.verify_on_stop_messaging_enabled", lambda config=None: True
    )
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    mark_workspace_edited(session_id="s1", cwd=root, paths=[str(root / "app.ts")])

    messages = _messages()
    agent = _GateAgent(messages, 0)
    agent._turn_file_mutation_paths = {str(root / "app.ts")}
    verdict = _apply(agent, "已修好", messages)
    assert verdict.continue_turn is True
    assert agent._verification_stop_nudges == 1  # edit gate fired once

    # Claim gate alone (no edits): fires once, then the shared counter stops it.
    messages2 = _messages()
    agent2 = _GateAgent(messages2, 0)
    assert _apply(agent2, "已发送", messages2).continue_turn is True
    assert agent2._verification_stop_nudges == 1
    assert _apply(agent2, "已发送", messages2).continue_turn is False
    assert agent2._verification_stop_nudges == 1  # still one, bounded
