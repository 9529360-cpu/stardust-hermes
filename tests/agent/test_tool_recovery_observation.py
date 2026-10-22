"""Repeated-failure guidance reuses terminal diagnostics and the existing counter."""
import json
import logging
from agent.tool_guardrails import ToolCallGuardrailController


def test_exact_failure_reports_existing_counter_and_latest_diagnostic(caplog):
    c = ToolCallGuardrailController()
    args = {"command": "missing"}
    result = json.dumps({"exit_code": 127, "hint": "Windows; shell=bash: check command -v"})
    with caplog.at_level(logging.INFO, logger="agent.tool_guardrails"):
        c.after_call("terminal", args, result)
        d = c.after_call("terminal", args, result)
    assert d.count == 2
    assert "Windows; shell=bash" in d.message
    assert "repeated_exact_failure_warning" in caplog.text
    assert "count=2" in caplog.text
    assert '"command"' not in caplog.text
