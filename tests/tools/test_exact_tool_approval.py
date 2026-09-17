from tools import approval
from tools import approval_context as context


def _recoverable_danger(monkeypatch):
    monkeypatch.setattr(approval, "_floor_block", lambda *args, **kwargs: None)
    monkeypatch.setattr(approval, "_yolo_active", lambda: False)
    monkeypatch.setattr(approval, "_command_matches_permanent_allowlist", lambda _command: False)
    monkeypatch.setattr(
        approval,
        "_tirith_scan",
        lambda _command: {"action": "allow", "findings": [], "summary": ""},
    )
    monkeypatch.setattr(
        approval,
        "detect_dangerous_command",
        lambda _command: (True, "danger:test", "test dangerous command"),
    )
    monkeypatch.setattr(approval, "is_approved", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        context,
        "_get_approval_config",
        lambda: {"mode": "manual", "single_query_mode": "deny"},
    )


def test_exact_tool_call_grant_crosses_unattended_guard_once(monkeypatch):
    _recoverable_danger(monkeypatch)
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")
    tokens = context.set_current_observability_context(tool_call_id="call-1")
    try:
        before = approval.check_all_command_guards("echo risky", "local")
        assert before["approved"] is False

        context.arm_exact_tool_call_approval("call-1")
        allowed = approval.check_all_command_guards("echo risky", "local")
        assert allowed["approved"] is True

        repeated = approval.check_all_command_guards("echo risky", "local")
        assert repeated["approved"] is False
    finally:
        context.reset_current_observability_context(tokens)


def test_exact_grant_does_not_leak_to_different_tool_call(monkeypatch):
    _recoverable_danger(monkeypatch)
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")
    context.arm_exact_tool_call_approval("approved-call")

    other = context.set_current_observability_context(tool_call_id="other-call")
    try:
        assert approval.check_all_command_guards("echo risky", "local")["approved"] is False
    finally:
        context.reset_current_observability_context(other)

    # The mismatch retires the stale grant; replaying the old id cannot resurrect it.
    original = context.set_current_observability_context(tool_call_id="approved-call")
    try:
        assert approval.check_all_command_guards("echo risky", "local")["approved"] is False
    finally:
        context.reset_current_observability_context(original)


def test_hard_floor_still_blocks_even_if_exact_grant_is_armed(monkeypatch):
    monkeypatch.setattr(
        approval,
        "_floor_block",
        lambda *args, **kwargs: {
            "approved": False,
            "status": "blocked",
            "message": "hard safety floor",
        },
    )
    tokens = context.set_current_observability_context(tool_call_id="call-floor")
    try:
        context.arm_exact_tool_call_approval("call-floor")
        result = approval.check_all_command_guards("irrelevant", "local")
        assert result["approved"] is False
        assert result["message"] == "hard safety floor"
    finally:
        # Retire the still-armed grant without relying on test process teardown.
        mismatch = context.set_current_observability_context(tool_call_id="cleanup-call")
        try:
            context._get_approval_mode()
        finally:
            context.reset_current_observability_context(mismatch)
        context.reset_current_observability_context(tokens)
