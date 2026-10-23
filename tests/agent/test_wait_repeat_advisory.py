"""Opt-in waiting advice uses observed outcomes, never dispatches or blocks work."""
import json
from types import SimpleNamespace

import pytest

from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController


ARGS = {"command": "gh pr checks 27 --watch --interval 10", "timeout": 1}
TIMEOUT = json.dumps({"exit_code": 124, "output": "Command timed out after 1 seconds"})
PENDING = json.dumps({"exit_code": 8, "output": "build\tpending\t0\thttps://example.test/check"})
DONE = json.dumps({"exit_code": 0, "output": "build\tpass\t1s\thttps://example.test/check"})


def controller(enabled=True):
    return ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping({
        "wait_repeat_advisory_enabled": enabled,
    }))


def test_advice_is_bounded_once_even_across_turns():
    c = controller()
    hints = []
    for _ in range(8):
        hints.append(c.observe_wait("terminal", ARGS, TIMEOUT))
        c.reset_for_turn()
        assert c.before_call("terminal", ARGS).allows_execution
        assert c.halt_decision is None
    assert sum(bool(h) for h in hints) == 1
    assert hints[0] is None
    assert "notify=true" in hints[1]
    assert "one-shot" in hints[1]
    assert len(hints[1]) <= 600
    assert "27" not in hints[1]  # no raw inputs/output in the hint


@pytest.mark.parametrize("result", [TIMEOUT, PENDING])
def test_changed_or_completed_result_rearms(result):
    c = controller()
    assert c.observe_wait("terminal", ARGS, result) is None
    assert c.observe_wait("terminal", ARGS, DONE) is None
    assert c.observe_wait("terminal", ARGS, result) is None


@pytest.mark.parametrize("tool,args,result", [
    ("patch", {"path": "src.py"}, '{"success":true}'),
    ("write_file", {"path": "src.py"}, '{"verified":true}'),
    ("terminal", {"command": "python edit.py"}, '{"exit_code":0}'),
    ("terminal", {**ARGS, "command": "gh pr checks 28 --watch"}, TIMEOUT),
])
def test_new_input_or_landed_edit_resets(tool, args, result):
    c = controller()
    assert c.observe_wait("terminal", ARGS, TIMEOUT) is None
    assert c.observe_wait(tool, args, result) is None
    assert c.observe_wait("terminal", ARGS, TIMEOUT) is None


def test_failed_edit_does_not_erase_wait_history():
    c = controller()
    c.observe_wait("terminal", ARGS, TIMEOUT)
    c.observe_wait("patch", {"path": "src.py"}, '{"error":"no match"}')
    assert c.observe_wait("terminal", ARGS, TIMEOUT)


@pytest.mark.parametrize("tool,args,result", [
    ("terminal", {"command": "pytest tests/test_slow.py"}, TIMEOUT),
    ("terminal", ARGS, '{"exit_code":1,"output":"authentication failed"}'),
    ("terminal", {**ARGS, "background": True}, TIMEOUT),
    ("terminal", {**ARGS, "command": "echo pending"}, PENDING),
    ("process_manage", {"action": "kill", "session_id": "x"}, '{"status":"running"}'),
])
def test_non_wait_or_error_not_classified(tool, args, result):
    c = controller()
    assert all(c.observe_wait(tool, args, result) is None for _ in range(5))


@pytest.mark.parametrize("tool,args,result", [
    ("process_manage", {"action": "wait", "session_id": "x"}, '{"status":"running","output":""}'),
    ("vendor_poll", {"id": "x"}, '{"status":"pending"}'),
    ("terminal", {"command": "kubectl wait --for=condition=Ready pod/x"}, TIMEOUT),
    ("terminal", {"command": "gh run watch 123"}, TIMEOUT),
])
def test_canonical_wait_and_poll_signatures(tool, args, result):
    c = controller()
    assert c.observe_wait(tool, args, result) is None
    assert c.observe_wait(tool, args, result)
    assert c.observe_wait(tool, args, result) is None


def test_same_pending_status_warns_once_but_changed_pending_is_new_evidence():
    c = controller()
    assert c.observe_wait("terminal", ARGS, PENDING) is None
    assert c.observe_wait("terminal", ARGS, PENDING)
    assert c.observe_wait("terminal", ARGS, PENDING) is None
    changed = PENDING.replace("build", "deploy")
    assert c.observe_wait("terminal", ARGS, changed) is None


def test_flag_defaults_off_and_parses_false():
    assert not ToolCallGuardrailConfig().wait_repeat_advisory_enabled
    for value in (False, "false", "off", 0):
        c = controller(value)
        assert all(c.observe_wait("terminal", ARGS, TIMEOUT) is None for _ in range(5))


def test_runtime_appends_only_one_hint_with_stall_guards_disabled():
    from run_agent import AIAgent

    a = SimpleNamespace(_tool_guardrails=controller(), _tool_guardrail_halt_decision=None)
    a._stall_guards_enabled = lambda: False
    a._set_tool_guardrail_halt = lambda decision: AIAgent._set_tool_guardrail_halt(a, decision)
    results = [AIAgent._append_guardrail_observation(a, "terminal", ARGS, TIMEOUT, failed=True)
               for _ in range(6)]
    assert sum("[Wait repeat advisory:" in r for r in results) == 1
    assert all(r.startswith(TIMEOUT) for r in results)
    assert a._tool_guardrail_halt_decision is None
