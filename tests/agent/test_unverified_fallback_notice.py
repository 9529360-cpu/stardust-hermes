"""Opt-in unverified notice on preserved budget-fallback answers (F5.1 / D5-04).

A verification gate can withhold a composed success claim, and when the iteration
budget then runs out ``_resolve_budget_fallback`` restores that claim verbatim:
``completed=False`` yet the visible text still reads as a verified success. With
``display.unverified_fallback_notice`` (or ``STARDUST_UNVERIFIED_FALLBACK_NOTICE``)
opted in, the restored candidate must carry a bilingual unverified notice; with the
flag off (the default) the output must stay byte-identical to the pre-flag behavior,
and normal ``text_response`` answers must never gain the notice.
"""

from types import SimpleNamespace

import pytest

from agent.turn_finalizer import _UNVERIFIED_FALLBACK_NOTICE, finalize_turn

CLAIM = "已修复并验证通过。add(2, 3) 现在返回 5，我已运行全部测试确认。"


class _FallbackAgent:
    """Minimal double for the budget-exhaustion finalize path (same shape as
    ``test_turn_finalizer_iteration_limit_exit._LimitAgent``)."""

    def __init__(self):
        self.max_iterations = 2
        self.iteration_budget = SimpleNamespace(remaining=0, used=2, max_total=2)
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "test-provider"
        self.base_url = ""
        self.session_id = "sess-f5"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        for key in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
        ):
            setattr(self, f"session_{key}", 0)
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self._handle_max_iterations_called = False

    def _handle_max_iterations(self, messages, api_call_count):
        self._handle_max_iterations_called = True
        return "summary from extra call"

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, messages):
        pass

    def _persist_session(self, messages, conversation_history):
        return True

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass


def _finalize(agent, *, final_response, api_call_count=2, exit_reason="unknown",
              failed=False, pending_verification_response=None, pending_previewed=False):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=api_call_count,
        interrupted=False,
        failed=failed,
        messages=[{"role": "user", "content": "task"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="task",
        original_user_message="task",
        _should_review_memory=False,
        _turn_exit_reason=exit_reason,
        _pending_verification_response=pending_verification_response,
        _pending_verification_response_previewed=pending_previewed,
    )


def _no_flag(monkeypatch):
    monkeypatch.delenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", raising=False)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})


def test_flag_on_env_appends_notice_and_keeps_completed_false(monkeypatch):
    monkeypatch.setenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    result = _finalize(_FallbackAgent(), final_response=None,
                       pending_verification_response=CLAIM)
    assert result["final_response"] == CLAIM + "\n\n" + _UNVERIFIED_FALLBACK_NOTICE
    assert result["completed"] is False
    assert result["turn_exit_reason"] == "max_iterations_reached(2/2)"


def test_flag_on_config_display_key_appends_notice(monkeypatch):
    _no_flag(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"display": {"unverified_fallback_notice": True}},
    )
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    result = _finalize(_FallbackAgent(), final_response=None,
                       pending_verification_response=CLAIM)
    assert result["final_response"].endswith(_UNVERIFIED_FALLBACK_NOTICE)
    assert result["final_response"].startswith(CLAIM)
    assert result["completed"] is False


def test_flag_off_default_output_is_byte_identical(monkeypatch):
    _no_flag(monkeypatch)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    result = _finalize(_FallbackAgent(), final_response=None,
                       pending_verification_response=CLAIM)
    assert result["final_response"] == CLAIM
    assert _UNVERIFIED_FALLBACK_NOTICE not in result["final_response"]
    assert result["completed"] is False


def test_env_off_overrides_config_on(monkeypatch):
    monkeypatch.setenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", "0")
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"display": {"unverified_fallback_notice": True}},
    )
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    result = _finalize(_FallbackAgent(), final_response=None,
                       pending_verification_response=CLAIM)
    assert result["final_response"] == CLAIM


def test_previewed_streamed_candidate_also_gets_notice(monkeypatch):
    monkeypatch.setenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _FallbackAgent()
    result = _finalize(agent, final_response=None,
                       pending_verification_response=CLAIM, pending_previewed=True)
    assert result["final_response"].endswith(_UNVERIFIED_FALLBACK_NOTICE)
    assert result["response_previewed"] is True
    assert result["completed"] is False


def test_normal_text_response_never_gains_notice_with_flag_on(monkeypatch):
    monkeypatch.setenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    result = _finalize(_FallbackAgent(), final_response=CLAIM, api_call_count=1,
                       exit_reason="text_response(finish_reason=stop)",
                       pending_verification_response=CLAIM)
    assert result["final_response"] == CLAIM
    assert result["completed"] is True


def test_unrelated_error_exit_never_gains_notice_with_flag_on(monkeypatch):
    """The pending value is the provenance guard: it must not mask later error exits."""
    monkeypatch.setenv("STARDUST_UNVERIFIED_FALLBACK_NOTICE", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _FallbackAgent()
    result = _finalize(agent, final_response=None, exit_reason="provider_failure",
                       failed=True, pending_verification_response=CLAIM)
    assert result["final_response"] is None
    assert agent._handle_max_iterations_called is False
