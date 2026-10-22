"""Wire-level check includes late request middleware additions."""
import pytest


def test_late_payload_expansion_is_rejected():
    from agent.request_input_budget import RequestInputBudgetExceeded, enforce_request_input_budget
    payload = {"messages": [{"role": "user", "content": "large " * 10000}],
               "tools": []}
    with pytest.raises(RequestInputBudgetExceeded):
        enforce_request_input_budget(payload, 1000)
    assert len(payload["messages"][0]["content"]) == 60000


def test_separate_system_and_tools_count_towards_budget():
    from agent.request_input_budget import RequestInputBudgetExceeded, enforce_request_input_budget
    for payload in [
        {"messages": [], "system": "x " * 10000},
        {"input": [], "instructions": "x " * 10000},
        {"messages": [], "tools": [{"type": "function", "function": {"name": "a", "description": "x " * 10000}}]},
    ]:
        with pytest.raises(RequestInputBudgetExceeded):
            enforce_request_input_budget(payload, 100)


def test_late_budget_failure_returns_without_provider_retry():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent.request_input_budget import RequestInputBudgetExceeded
    from agent.turn_api_error import handle_api_error
    agent = SimpleNamespace(iteration_budget=SimpleNamespace(refund=Mock()),
                            _persist_session=Mock(), _api_call_count=1)
    result = handle_api_error(
        agent, api_error=RequestInputBudgetExceeded("budget exceeded"), _retry=None,
        thinking_spinner=None, messages=[{"role": "user", "content": "retained task"}],
        api_messages=[], api_kwargs={}, system_message=None, active_system_prompt=None,
        conversation_history=None, approx_tokens=1001, retry_count=0, max_retries=3,
        compression_attempts=0, max_compression_attempts=3, api_call_count=1,
        api_request_id="test", api_start_time=0, effective_task_id="test", turn_id="test",
    )
    assert result.action == "return"
    assert result.result["failure_reason"] == "request_input_budget_exceeded"
    assert result.result["api_calls"] == 0
    assert not result.result["failure_retryable"]
    assert result.result["messages"][0]["content"] == "retained task"


def test_default_does_not_reject_legacy_requests():
    from agent.request_input_budget import enforce_request_input_budget
    enforce_request_input_budget({"messages": [{"role": "user", "content": "x" * 10000}]}, 0)
