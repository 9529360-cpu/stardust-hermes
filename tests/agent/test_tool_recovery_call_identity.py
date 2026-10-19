"""Regression coverage for per-call JSON recovery and handled retry rounds."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.turn_tool_validation import validate_tool_calls
from agent.turn_tool_round import run_tool_round


def make_agent():
    a = Mock()
    a.valid_tool_names = {"terminal"}
    a.log_prefix = "test"
    a.quiet_mode = True
    a.verbose_logging = False
    a._invalid_tool_retry_counts = {}
    a._invalid_json_retries = 0
    a._repair_tool_call.return_value = None
    return a


def call(call_id, args, name="terminal"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args))


def test_valid_same_name_sibling_is_not_marked_as_invalid_json():
    a = make_agent()
    bad = call("bad", '{"command":,}')
    good = call("good", '{"command":"echo ok"}')
    v = validate_tool_calls(a, SimpleNamespace(tool_calls=[bad, good]), "tool_calls",
                            messages=[], conversation_history=[], api_call_count=1,
                            effective_task_id=None)
    assert v.action == "ok"
    assert a._executable_tool_calls_this_round == [good]
    assert set(a._json_error_results_this_round) == {"bad"}


def test_invalid_name_handled_retry_never_enters_round_dispatch():
    a = make_agent()
    # Validation creates an empty JSON error map even for an invalid-name retry.
    a._build_assistant_message.return_value = {"role": "assistant", "content": ""}
    a._cap_delegate_task_calls.side_effect = AssertionError("handled retry reached dispatch")
    result = run_tool_round(
        a, assistant_message=SimpleNamespace(tool_calls=[call("bad", "{}", "nonexistent")]),
        finish_reason="tool_calls", messages=[], conversation_history=[], api_call_count=1,
        effective_task_id=None, user_message="test", system_message={}, active_system_prompt="",
        compression_attempts=0, max_compression_attempts=3, final_response="", failed=False,
        _turn_exit_reason=None, truncated_tool_call_retries=0,
    )
    assert result.action == "continue"
    a._cap_delegate_task_calls.assert_not_called()


def test_json_recovery_cannot_restore_guardrail_removed_call():
    a = make_agent()
    bad = call("bad", '{"command":,}')
    good = call("good", '{"command":"echo ok"}')
    a._cap_delegate_task_calls.return_value = [bad]
    a._deduplicate_tool_calls.side_effect = lambda calls: calls
    a._flush_messages_to_session_db.return_value = True
    a._execute_tool_calls.side_effect = AssertionError("removed call restored")
    with patch("agent.turn_tool_round.stage_tool_call_message", return_value=(
        {"role": "assistant", "content": ""}, False
    )):
        result = run_tool_round(
            a, assistant_message=SimpleNamespace(tool_calls=[bad, good]),
            finish_reason="tool_calls", messages=[], conversation_history=[], api_call_count=1,
            effective_task_id=None, user_message="test", system_message={}, active_system_prompt="",
            compression_attempts=0, max_compression_attempts=3, final_response="", failed=False,
            _turn_exit_reason=None, truncated_tool_call_retries=0,
        )
    assert result.action == "continue"
    a._execute_tool_calls.assert_not_called()


def test_multiple_missing_ids_have_distinct_json_error_results():
    from agent.message_sanitization import uniquify_tool_call_ids
    a = make_agent()
    a._uniquify_tool_call_ids.side_effect = uniquify_tool_call_ids
    calls = [call("", '{"command":,}'), call(None, '{"command":,}')]
    validate_tool_calls(a, SimpleNamespace(tool_calls=calls), "tool_calls",
                        messages=[], conversation_history=[], api_call_count=1,
                        effective_task_id=None)
    assert all(tc.id for tc in calls)
    assert len({tc.id for tc in calls}) == 2
    assert len(a._json_error_results_this_round) == 2
