"""DL-P2.8f — the max-iterations summary must not ship an unproven "done" claim.

The iteration-budget summary is composed in finalize, after the loop has
exited, so it never passes the stop gate (8e finding). When
``agent.verify_on_stop_messaging`` is ON and the summary asserts a completed
action with no successful tool result from this turn, the deterministic
post-check appends the plain unverified note to the returned text AND the
transcript row (they stay byte-identical). Flag OFF (default), no claim, real
evidence, non-messaging surface, or an explicit user opt-out leave the summary
byte-identical. No new loop, no extra model call, no extra iteration.
"""

import pytest

from agent.turn_stop_gates import apply_stop_gates  # noqa: F401  (shared-counter precedent)
from agent.verification_stop import (
    _MAXITER_UNVERIFIED_NOTE,
    contains_completion_claim,
    max_iterations_unverified_note,
    verify_on_stop_messaging_enabled,
)


FLAG_ON = {"agent": {"verify_on_stop_messaging": True}}
FLAG_OFF = {"agent": {}}

CLAIM_SUMMARY = "已清理指定目录并删除了旧构建产物，任务收尾完成。"
NO_CLAIM_SUMMARY = "目前还没有开始清理，仍在定位仓库位置。"


@pytest.fixture(autouse=True)
def _messaging_surface(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDUST_SESSION_PLATFORM", "telegram")
    for var in ("STARDUST_VERIFY_ON_STOP", "STARDUST_PLATFORM", "STARDUST_SESSION_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))


def _messages(tool_rows=None):
    rows = [{"role": "user", "content": "帮我把临时构建产物清掉"}]
    rows.extend(tool_rows or [])
    return rows


def _tool_row(name="terminal", content='{"success": true, "output": "cleaned"}', call_id="c1"):
    return {"role": "tool", "name": name, "content": content, "tool_call_id": call_id}


def _note(summary, messages, config=FLAG_ON, user_idx=0, user_message=None):
    return max_iterations_unverified_note(
        summary_text=summary, messages=messages,
        current_turn_user_idx=user_idx,
        user_message=messages[user_idx].get("content") if user_message is None and isinstance(user_idx, int) and 0 <= user_idx < len(messages) else user_message,
        config=config,
    )


# ── pure helper ─────────────────────────────────────────────────────────


def test_claim_without_evidence_returns_note():
    assert _note(CLAIM_SUMMARY, _messages()) == _MAXITER_UNVERIFIED_NOTE


def test_claim_with_evidence_returns_none():
    assert _note(CLAIM_SUMMARY, _messages([_tool_row()])) is None


def test_no_claim_returns_none():
    assert _note(NO_CLAIM_SUMMARY, _messages()) is None


def test_flag_off_returns_none():
    assert _note(CLAIM_SUMMARY, _messages(), config=FLAG_OFF) is None


def test_user_opt_out_respected():
    rows = [{"role": "user", "content": "基于现有结果直接收尾，不要重跑"}]
    assert _note(CLAIM_SUMMARY, rows, user_message=rows[0]["content"]) is None


def test_non_messaging_surface_returns_none(monkeypatch):
    import agent.verification_stop as vs

    monkeypatch.setattr(vs, "_session_is_messaging_surface", lambda: False)
    assert _note(CLAIM_SUMMARY, _messages()) is None


def test_note_is_not_itself_a_claim():
    assert contains_completion_claim(_MAXITER_UNVERIFIED_NOTE) is None


def test_flag_reading_delegates_to_shared_flag():
    # same flag object as the 8e gate — one switch covers both paths
    assert verify_on_stop_messaging_enabled(FLAG_ON) is True
    assert verify_on_stop_messaging_enabled(FLAG_OFF) is False


# ── handle_max_iterations wiring ────────────────────────────────────────


class _SummaryAgent:
    """Minimal stand-in with just what handle_max_iterations reads."""

    def __init__(self, messages, user_idx=0, max_iterations=2):
        self.max_iterations = max_iterations
        self.api_mode = "chat_completions"
        self._persist_user_message_idx = user_idx
        self.messages = messages
        self.client = None
        self.model = "test-model"
        self.quiet_mode = True
        self.suppress_status_output = True


def _run_summary(monkeypatch, agent, summary_text):
    """Drive the real handle_max_iterations with a stubbed summary attempt."""
    from agent import chat_completion_helpers as cch

    monkeypatch.setattr(cch, "_iteration_summary_api_messages", lambda a, m: list(m))
    monkeypatch.setattr(
        cch, "_chat_summary_attempt",
        lambda a, m, rid: (lambda retry_count: summary_text),
    )
    return cch.handle_max_iterations(agent, agent.messages, 2)


def _flag_patcher(monkeypatch, enabled):
    return monkeypatch.setattr(
        "agent.verification_stop.verify_on_stop_messaging_enabled",
        lambda config=None: enabled,
    )


def test_summary_claim_without_evidence_gets_note(monkeypatch):
    _flag_patcher(monkeypatch, True)
    agent = _SummaryAgent(_messages())
    result = _run_summary(monkeypatch, agent, CLAIM_SUMMARY)
    assert result == CLAIM_SUMMARY.rstrip() + "\n\n" + _MAXITER_UNVERIFIED_NOTE
    # returned text and the transcript row are byte-identical
    assert agent.messages[-1]["content"] == result


def test_summary_with_evidence_unchanged(monkeypatch):
    _flag_patcher(monkeypatch, True)
    agent = _SummaryAgent(_messages([_tool_row()]))
    assert _run_summary(monkeypatch, agent, CLAIM_SUMMARY) == CLAIM_SUMMARY


def test_summary_no_claim_unchanged(monkeypatch):
    _flag_patcher(monkeypatch, True)
    agent = _SummaryAgent(_messages())
    assert _run_summary(monkeypatch, agent, NO_CLAIM_SUMMARY) == NO_CLAIM_SUMMARY


def test_summary_flag_off_byte_identical(monkeypatch):
    _flag_patcher(monkeypatch, False)
    agent = _SummaryAgent(_messages())
    assert _run_summary(monkeypatch, agent, CLAIM_SUMMARY) == CLAIM_SUMMARY
    assert agent.messages[-1]["content"] == CLAIM_SUMMARY


def test_summary_user_opt_out_unchanged(monkeypatch):
    _flag_patcher(monkeypatch, True)
    rows = [{"role": "user", "content": "就基于现有结果收尾，不用再验证"}]
    agent = _SummaryAgent(rows)
    assert _run_summary(monkeypatch, agent, CLAIM_SUMMARY) == CLAIM_SUMMARY


def test_summary_failure_copy_not_noted(monkeypatch):
    """The no-summary fallback text has no completion claim — unchanged."""
    _flag_patcher(monkeypatch, True)
    agent = _SummaryAgent(_messages())
    from agent.turn_failure_copy import site_copy

    fallback = site_copy("max_iterations_no_summary", limit=2)
    assert _run_summary(monkeypatch, agent, fallback) == fallback
