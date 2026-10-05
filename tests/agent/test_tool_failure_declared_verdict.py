"""A tool's own success flag decides whether its result is a failure.

Both classifiers — the display/log/guardrail input (``_detect_tool_failure``)
and the guardrail fallback (``classify_tool_failure``) — used to sniff the
first 500 characters for ``"failed"``/``"error"``. Success payloads name those
words legitimately, so every successful ``assistant_tasks`` create (which
reports an empty ``failed`` list) was logged as a tool error and counted toward
the same-tool failure loop warning.
"""

import json

import pytest

from agent.display import _detect_tool_failure
from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController, classify_tool_failure
from tools import assistant_tasks

CLASSIFIERS = pytest.mark.parametrize("classify", [_detect_tool_failure, classify_tool_failure])


def _created_task_result(monkeypatch) -> str:
    """The real ``assistant_tasks`` create response for one successfully queued task."""
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "tools.kanban_tools._handle_create",
        lambda args: json.dumps({"ok": True, "task_id": "t_1", "status": "ready", "subscribed": True}),
    )
    return assistant_tasks.assistant_tasks_tool(
        action="create",
        session_id="session-1",
        request_id="call-1",
        tasks=[{"title": "Convert report", "instruction": "Convert report.docx to PDF and verify it opens."}],
    )


@CLASSIFIERS
def test_successful_assistant_task_create_is_not_a_failure(classify, monkeypatch):
    result = _created_task_result(monkeypatch)
    assert json.loads(result)["failed"] == []  # the word the old sniff tripped on

    assert classify("assistant_tasks", result) == (False, "")


@CLASSIFIERS
def test_declared_success_outranks_failure_words_in_the_payload(classify):
    listing = json.dumps({"success": True, "count": 1, "jobs": [{"name": "digest", "last_status": "error"}]})

    assert classify("cronjob", listing) == (False, "")


@CLASSIFIERS
@pytest.mark.parametrize("payload", [
    {"ok": False, "created": [{"index": 0}], "failed": [{"index": 1, "error": "bad task"}]},
    {"ok": False, "reason": "board unavailable"},
    {"success": False, "message": "nothing to do"},
    {"success": True, "ok": False},
])
def test_declared_failure_is_a_failure(classify, payload):
    is_failure, _suffix = classify("assistant_tasks", json.dumps(payload))

    assert is_failure is True


@CLASSIFIERS
def test_undeclared_results_keep_the_error_heuristic(classify):
    assert classify("web_extract", json.dumps({"results": [{"url": "u", "error": "403"}]}))[0] is True
    assert classify("web_extract", json.dumps({"results": [{"url": "u", "content": "ok"}]})) == (False, "")


@pytest.mark.parametrize("executor_verdict", [True, False], ids=["executor-path", "fallback-path"])
def test_successful_creates_in_one_turn_raise_no_loop_warning(monkeypatch, executor_verdict):
    """tool_executor passes ``failed=_detect_tool_failure(...)``; other callers use the fallback."""
    result = _created_task_result(monkeypatch)
    controller = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))
    failed = _detect_tool_failure("assistant_tasks", result)[0] if executor_verdict else None

    decisions = [
        controller.after_call("assistant_tasks", {"action": "create", "n": n}, result, failed=failed)
        for n in range(8)
    ]

    assert [decision.action for decision in decisions] == ["allow"] * 8
