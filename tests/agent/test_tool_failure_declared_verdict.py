"""Declared tool success outranks failure words in payloads and loop guardrails."""

import json

import pytest

from agent.display import _detect_tool_failure
from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController, classify_tool_failure

CLASSIFIERS = pytest.mark.parametrize("classify", [_detect_tool_failure, classify_tool_failure])


def _successful_result() -> str:
    """A declared-success payload can legitimately describe earlier failures."""
    return json.dumps({"ok": True, "created": [{"id": "1"}], "failed": []})


@CLASSIFIERS
def test_successful_result_with_empty_failed_list_is_not_a_failure(classify):
    result = _successful_result()
    assert json.loads(result)["failed"] == []  # the word the old sniff tripped on

    assert classify("test_task", result) == (False, "")


@CLASSIFIERS
def test_declared_success_outranks_failure_words_in_the_payload(classify):
    listing = json.dumps({"success": True, "count": 1, "jobs": [{"name": "digest", "last_status": "error"}]})

    assert classify("cronjob", listing) == (False, "")


@CLASSIFIERS
@pytest.mark.parametrize("payload", [
    {"ok": False, "created": [{"index": 0}], "failed": [{"index": 1, "error": "bad task"}]},
    {"ok": False, "reason": "service unavailable"},
    {"success": False, "message": "nothing to do"},
    {"success": True, "ok": False},
])
def test_declared_failure_is_a_failure(classify, payload):
    is_failure, _suffix = classify("test_task", json.dumps(payload))

    assert is_failure is True


@CLASSIFIERS
def test_undeclared_results_keep_the_error_heuristic(classify):
    assert classify("web_extract", json.dumps({"results": [{"url": "u", "error": "403"}]}))[0] is True
    assert classify("web_extract", json.dumps({"results": [{"url": "u", "content": "ok"}]})) == (False, "")


@pytest.mark.parametrize("executor_verdict", [True, False], ids=["executor-path", "fallback-path"])
def test_successful_creates_in_one_turn_raise_no_loop_warning(executor_verdict):
    """tool_executor passes ``failed=_detect_tool_failure(...)``; other callers use the fallback."""
    result = _successful_result()
    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))
    failed = _detect_tool_failure("test_task", result)[0] if executor_verdict else None

    decisions = [
        controller.after_call("test_task", {"action": "create", "n": n}, result, failed=failed)
        for n in range(8)
    ]

    assert [decision.action for decision in decisions] == ["allow"] * 8
