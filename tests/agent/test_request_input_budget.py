"""Opt-in budget stops oversized requests even when normal compression defers."""
from types import SimpleNamespace
from unittest.mock import Mock

from agent.turn_preflight_gate import run_preflight_gate


def gate(agent, tokens, **overrides):
    args = dict(request_pressure_tokens=tokens, _moa_prepared_request=None,
                pending_moa_prepared_request=None,
                messages=[{"role": "system", "content": "system"},
                          {"role": "user", "content": "task"}],
                system_message="system", user_message="task", active_system_prompt="system",
                conversation_history=None, api_call_count=1, compression_attempts=0,
                max_compression_attempts=3, effective_task_id="budget-test",
                final_response=None, failed=False, _turn_exit_reason=None,
                _compression_timeout_exhausted=False, _preflight_compression_blocked=False,
                _provider_overflow_recovery_pending=False, _last_preflight_pressure=None)
    args.update(overrides)
    return run_preflight_gate(agent, **args)


def agent_for_budget(budget=1000):
    compressor = SimpleNamespace(threshold_tokens=50000,
                                 should_compress=lambda t: False,
                                 should_defer_preflight_to_real_usage=lambda t: True,
                                 get_active_compression_failure_cooldown=lambda: None)
    return SimpleNamespace(request_input_budget_tokens=budget, context_compressor=compressor,
                           compression_enabled=True, provider="custom", model="test",
                           _request_pressure_anchored=False, _compress_context=Mock(),
                           _emit_status=Mock(), iteration_budget=SimpleNamespace(refund=Mock()),
                           _persist_session=Mock(), log_prefix="", _api_call_count=1)


def test_budget_cannot_be_bypassed_by_disabled_compression():
    agent = agent_for_budget()
    agent.compression_enabled = False
    result = gate(agent, 1001)
    assert result.action == "break"
    assert result._turn_exit_reason == "request_input_budget_exceeded"
    assert result.failed
    assert result.api_call_count == 0
    assert "1001" in result.final_response and "1000" in result.final_response
    agent._compress_context.assert_not_called()


def test_budget_compresses_despite_noisy_estimate_deferral():
    agent = agent_for_budget()
    agent._compress_context.return_value = ([{"role": "user", "content": "task"}], "system")
    result = gate(agent, 1001)
    assert result.action == "continue"
    assert result.compression_attempts == 1
    assert result.api_call_count == 0


def test_exhausted_or_ineffective_budget_compression_stops():
    for extra in [dict(compression_attempts=3), dict(_preflight_compression_blocked=True)]:
        agent = agent_for_budget()
        result = gate(agent, 1001, **extra)
        assert result.action == "break"
        assert result._turn_exit_reason == "request_input_budget_exceeded"
        agent._compress_context.assert_not_called()


def test_budget_boundary_and_default_leave_normal_policy():
    for budget, tokens in [(1000, 1000), (0, 1001)]:
        agent = agent_for_budget(budget)
        assert gate(agent, tokens).action == "fallthrough"
        agent._compress_context.assert_not_called()
