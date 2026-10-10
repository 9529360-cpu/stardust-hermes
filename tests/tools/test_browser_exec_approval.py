"""browser_exec runs Python on the agent host, so its approval gate is tested end to end: through the tool
registry, with the real approval engine and a temporary HERMES_HOME, and directly for what the prompt shows."""

import json
import subprocess

import pytest

import tools.browser_use_cli as bu_cli
from tools import approval
from tools.approval import request_tool_approval as _real_request_tool_approval


@pytest.fixture
def standing_approvals(monkeypatch, tmp_path):
    """Permanent approvals for one test only: HERMES_HOME is temporary and the allowlist is in memory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    patterns: set = set()
    monkeypatch.setattr(approval, "_permanent_set", lambda: patterns)
    return patterns


def _as_dict(out):
    return out if isinstance(out, dict) else json.loads(out)


class TestPromptAndValidation:
    def test_prompt_shows_the_whole_program(self, monkeypatch):
        seen = {}

        def record(tool_name, reason, **kwargs):
            seen.update(tool_name=tool_name, reason=reason, **kwargs)
            return {"approved": False, "message": "no"}

        monkeypatch.setattr("tools.approval.request_tool_approval", record)
        program = "x = 1\n" * 200 + "print('the last line the user must see')"
        bu_cli.browser_exec(program)
        assert seen["tool_name"] == "browser_exec"
        assert seen["rule_key"] == "browser_exec_host_python"
        assert "print('the last line the user must see')" in seen["reason"]

    def test_prompt_redacts_a_token_before_display(self, monkeypatch):
        seen = {}

        def record(tool_name, reason, **kwargs):
            seen["reason"] = reason
            return {"approved": False, "message": "no"}

        monkeypatch.setattr("tools.approval.request_tool_approval", record)
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        bu_cli.browser_exec(f"token = '{token}'\nprint(1)")
        assert token not in seen["reason"]
        assert "print(1)" in seen["reason"]

    def test_invalid_session_is_rejected_before_any_prompt(self, monkeypatch):
        monkeypatch.setattr("tools.approval.request_tool_approval",
                            lambda *a, **k: pytest.fail("no prompt for an invalid call"))
        result = json.loads(bu_cli.browser_exec("print(1)", session="bad name!"))
        assert result["success"] is False
        assert result["error_type"] == "invalid_session"

    def test_empty_code_is_rejected_before_any_prompt(self, monkeypatch):
        monkeypatch.setattr("tools.approval.request_tool_approval",
                            lambda *a, **k: pytest.fail("no prompt for empty code"))
        result = json.loads(bu_cli.browser_exec("   "))
        assert result["success"] is False
        assert result["error_type"] == "invalid_request"


class TestRegistryDispatch:
    """Through the tool registry with the real approval engine: nothing is patched except the CLI."""

    @pytest.fixture(autouse=True)
    def _real_engine(self, monkeypatch):
        monkeypatch.setattr("tools.approval.request_tool_approval", _real_request_tool_approval)

    def test_no_human_and_no_standing_approval_is_denied_and_nothing_launches(self, monkeypatch, standing_approvals):
        from tools.registry import discover_builtin_tools, registry

        discover_builtin_tools()
        monkeypatch.setattr(bu_cli, "_find_cli", lambda: pytest.fail("the CLI must not launch without approval"))
        result = _as_dict(registry.dispatch("browser_exec", {"code": "print('hi')"}))
        assert result["success"] is False
        assert result["error_type"] == "approval_denied"

    def test_standing_approval_lets_dispatch_reach_the_cli(self, monkeypatch, standing_approvals):
        from tools.registry import discover_builtin_tools, registry

        discover_builtin_tools()
        standing_approvals.add("plugin_rule:browser_exec_host_python")
        launched = {}
        monkeypatch.setattr(bu_cli, "_find_cli", lambda: ["browser-use"])
        monkeypatch.setattr(bu_cli, "_base_subprocess_env", lambda: {})
        monkeypatch.setattr(bu_cli, "_route_backend", lambda *args: None)
        monkeypatch.setattr(bu_cli, "_attach_vault_supervisor", lambda *args: None)
        monkeypatch.setattr(bu_cli, "_workspace_dir", lambda task_id: None)
        monkeypatch.setattr(
            bu_cli,
            "_run_cli_killing_process_group",
            lambda cmd, code, env, timeout: (
                launched.update(code=code) or subprocess.CompletedProcess(cmd, 0, "ok", "")
            ),
        )
        result = _as_dict(registry.dispatch("browser_exec", {"code": "print('hi')"}))
        assert result["success"] is True
        assert "print('hi')" in launched["code"]
