from tools import approval
from tools import approval_context
from tools import background_terminal_approval as bridge
from tools import terminal_tool


def _local_terminal(monkeypatch):
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "local"})
    monkeypatch.setattr(terminal_tool, "_docker_has_host_access", lambda _config: False)
    monkeypatch.setattr(approval, "_should_skip_container_guards", lambda *args, **kwargs: False)
    monkeypatch.setattr(approval, "_floor_block", lambda *args, **kwargs: None)
    monkeypatch.setattr(approval, "_yolo_active", lambda: False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(approval, "_command_matches_permanent_allowlist", lambda _command: False)
    monkeypatch.setattr(
        approval,
        "_tirith_scan",
        lambda _command: {"action": "allow", "findings": [], "summary": ""},
    )
    monkeypatch.setattr(approval, "is_approved", lambda *_args, **_kwargs: False)


def test_safe_terminal_call_needs_no_durable_confirmation(monkeypatch):
    _local_terminal(monkeypatch)
    monkeypatch.setattr(
        approval,
        "detect_dangerous_command",
        lambda _command: (False, None, ""),
    )
    result = bridge.inspect_terminal_approval({"command": "git status"})
    assert result.requires_approval is False
    assert result.block_message == ""


def test_recoverable_danger_becomes_exact_durable_confirmation(monkeypatch):
    _local_terminal(monkeypatch)
    monkeypatch.setattr(
        approval,
        "detect_dangerous_command",
        lambda _command: (True, "danger:rm", "recursive delete"),
    )
    result = bridge.inspect_terminal_approval({"command": "rm -rf ./build"})
    assert result.requires_approval is True
    assert result.rule_key == "stardust:terminal-risk"
    assert "recursive delete" in result.reason
    assert "rm -rf ./build" not in result.reason


def test_tirith_warning_also_requires_durable_confirmation(monkeypatch):
    _local_terminal(monkeypatch)
    monkeypatch.setattr(
        approval,
        "detect_dangerous_command",
        lambda _command: (False, None, ""),
    )
    monkeypatch.setattr(
        approval,
        "_tirith_scan",
        lambda _command: {
            "action": "warn",
            "findings": [{
                "rule_id": "T001",
                "severity": "HIGH",
                "title": "Suspicious shell behavior",
                "description": "review before execution",
            }],
            "summary": "warning",
        },
    )
    result = bridge.inspect_terminal_approval({"command": "opaque-script"})
    assert result.requires_approval is True
    assert "Suspicious shell behavior" in result.reason


def test_unconditional_terminal_floor_is_not_made_user_approvable(monkeypatch):
    _local_terminal(monkeypatch)
    monkeypatch.setattr(
        approval,
        "_floor_block",
        lambda *args, **kwargs: {"approved": False, "message": "hardline blocked"},
    )
    result = bridge.inspect_terminal_approval({"command": "forbidden"})
    assert result.requires_approval is False
    assert result.block_message == "hardline blocked"


def test_isolated_container_keeps_existing_terminal_policy_without_extra_prompt(monkeypatch):
    monkeypatch.setattr(
        terminal_tool,
        "_get_env_config",
        lambda: {"env_type": "docker", "docker_mount_cwd_to_workspace": False},
    )
    monkeypatch.setattr(terminal_tool, "_docker_has_host_access", lambda _config: False)
    monkeypatch.setattr(approval, "_should_skip_container_guards", lambda *args, **kwargs: True)
    result = bridge.inspect_terminal_approval({"command": "rm -rf ./sandbox-output"})
    assert result == bridge.TerminalApprovalRequirement()


def test_existing_bypass_or_allowlist_does_not_add_durable_prompt(monkeypatch):
    _local_terminal(monkeypatch)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "off")
    result = bridge.inspect_terminal_approval({"command": "anything"})
    assert result.requires_approval is False
