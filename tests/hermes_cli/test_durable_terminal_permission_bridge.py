from pathlib import Path

import pytest

from hermes_cli import assistant_permissions as permissions
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools import approval
from tools import approval_context
from tools import background_task_approval
from tools.background_terminal_approval import TerminalApprovalRequirement


@pytest.fixture
def running_task(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    identities = getattr(kb, "_INITIALIZED_FILE_IDENTITIES", None)
    if identities is not None:
        identities.pop(str(db_path.resolve()), None)
    kb.init_db()

    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="Risky background terminal work", assignee="default")
        assert kb.claim_task(conn, task_id) is not None
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setattr(permissions, "_durable_worker_confirmation_active", lambda: True)
    return task_id


def _require_terminal_consent(monkeypatch):
    monkeypatch.setattr(
        "tools.background_terminal_approval.inspect_terminal_approval",
        lambda _args: TerminalApprovalRequirement(
            requires_approval=True,
            reason="Existing terminal security checks require consent for this exact call.",
            rule_key="stardust:terminal-risk",
        ),
    )


def _dangerous_terminal_guard(monkeypatch):
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
        approval_context,
        "_get_approval_config",
        lambda: {"mode": "manual", "single_query_mode": "deny"},
    )


def test_risky_background_terminal_pauses_then_exact_retry_runs_once(running_task, monkeypatch):
    _require_terminal_consent(monkeypatch)
    _dangerous_terminal_guard(monkeypatch)
    args = {"command": "echo risky", "workdir": "/tmp/example"}

    first = permissions.pre_tool_call_directive("terminal", args, tool_call_id="call-first")
    assert first is not None
    assert first["action"] == "block"

    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, running_task)
        assert task.status == "blocked"
        pending = background_task_approval.list_approvals(conn, running_task, pending_only=True)
        assert len(pending) == 1
        approval_id = pending[0]["approval_id"]
        decided = background_task_approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=approval_id
        )
        assert decided["decision"] == "approved"
        assert decided["resumed"] is True
        assert kb.claim_task(conn, running_task) is not None

    # A new worker/model tool call id is expected after durable resume. The args fingerprint, not
    # the old call id, is what matches the durable grant; the new id scopes the terminal bypass once.
    retry = permissions.pre_tool_call_directive("terminal", args, tool_call_id="call-retry")
    assert retry is None

    tokens = approval_context.set_current_observability_context(tool_call_id="call-retry")
    try:
        allowed = approval.check_all_command_guards("echo risky", "local")
        assert allowed["approved"] is True

        # The exact runtime grant is gone after one terminal security check.
        repeated = approval.check_all_command_guards("echo risky", "local")
        assert repeated["approved"] is False
    finally:
        approval_context.reset_current_observability_context(tokens)

    with kbc.connect_closing() as conn:
        states = background_task_approval.list_approvals(conn, running_task)
        assert states[0]["state"] == "consumed"


def test_changed_terminal_args_cannot_consume_approved_grant(running_task, monkeypatch):
    _require_terminal_consent(monkeypatch)
    original = {"command": "echo version-one"}
    first = permissions.pre_tool_call_directive("terminal", original, tool_call_id="call-one")
    assert first and first["action"] == "block"

    with kbc.connect_closing() as conn:
        pending = background_task_approval.list_approvals(conn, running_task, pending_only=True)
        background_task_approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=pending[0]["approval_id"]
        )
        assert kb.claim_task(conn, running_task) is not None

    changed = permissions.pre_tool_call_directive(
        "terminal", {"command": "echo version-two"}, tool_call_id="call-two"
    )
    assert changed is not None
    assert changed["action"] == "block"

    with kbc.connect_closing() as conn:
        states = background_task_approval.list_approvals(conn, running_task)
        assert len(states) == 2
        assert {item["state"] for item in states} == {"approved", "pending"}
        assert states[0]["args_sha256"] != states[1]["args_sha256"]


def test_risky_terminal_without_tool_call_identity_fails_closed(running_task, monkeypatch):
    _require_terminal_consent(monkeypatch)
    result = permissions.pre_tool_call_directive("terminal", {"command": "echo risky"})
    assert result is not None
    assert result["action"] == "block"
    assert "tool call identity" in result["message"]

    with kbc.connect_closing() as conn:
        assert background_task_approval.list_approvals(conn, running_task) == []
