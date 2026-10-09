"""Approval ledger behavior against the isolated HERMES_HOME fixture."""
import json

import pytest

from hermes_constants import get_hermes_home
from tools import approval, approval_audit as audit


def read_audit(**kwargs):
    assert audit.flush_approval_audit()
    return audit.read_approval_audit(**kwargs)


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(approval, "get_current_session_key", lambda: "audit-session")
    monkeypatch.setattr(approval, "_yolo_active", lambda: False)
    monkeypatch.setattr(approval, "is_approved", lambda *a: False)
    monkeypatch.setattr(approval.approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(approval, "_presence", lambda cb=None: (cb, True, False, False))
    monkeypatch.setattr(approval, "_persist_choice", lambda *a: None)
    return lambda: approval.request_tool_approval("write_file", "sensitive write")


@pytest.mark.parametrize("choice,outcome", [("once", "approved_once"), ("session", "approved_session"),
                                            ("always", "approved_permanent"), ("deny", "denied")])
def test_human_decisions(gate, monkeypatch, choice, outcome):
    monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: choice)
    result = gate()
    assert result["approved"] == (choice != "deny")
    entries = read_audit()
    assert len(entries) == 1
    assert entries[0]["outcome"] == outcome
    assert entries[0]["kind"] == "tool"
    assert entries[0]["tool_name"] == "write_file"
    assert entries[0]["session_key"] == "audit-session"


def append(session="one", preview="echo hello"):
    audit.write_approval_audit(session_key=session, kind="command", tool_name="terminal",
                               description=preview, pattern_key="test", outcome="approved_once",
                               mode="manual", command_preview=preview)


def test_redaction_and_truncation(monkeypatch):
    secret = "sk-" + "Ab1" * 20
    append(preview=f"curl https://user:password@example.test?api_key={secret} " + "x" * 500)
    text = (get_hermes_home() / "audit/approvals.jsonl").read_text()
    assert secret not in text
    assert "user:password" not in text
    assert len(json.loads(text)["command_preview"]) <= 300


def test_filter_limit_and_rotation(monkeypatch):
    monkeypatch.setattr(audit, "MAX_AUDIT_BYTES", 600)
    append("one")
    append("two")
    append("one", "last")
    assert (get_hermes_home() / "audit/approvals.jsonl.1").exists()
    assert read_audit(limit=1)[0]["command_preview"] == "last"
    assert all(e["session_key"] == "one" for e in read_audit(session_key="one"))
    assert read_audit(limit=0) == []


def test_write_failure_keeps_gate_working(gate, monkeypatch):
    monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "once")
    directory = get_hermes_home() / "audit"
    directory.write_text("not a directory")
    assert gate()["approved"] is True


def test_floor_and_yolo(gate, monkeypatch):
    assert not approval.check_dangerous_command("rm -rf /", "local")["approved"]
    assert read_audit()[0]["mode"] == "floor"
    monkeypatch.setattr(approval, "_yolo_active", lambda: True)
    assert gate()["approved"]
    assert read_audit()[0]["mode"] == "yolo"


def test_smart_and_unattended(gate, monkeypatch):
    monkeypatch.setattr(approval, "_smart_verdict", lambda *a: "approve")
    monkeypatch.setattr(approval.approval_context, "_get_approval_mode", lambda: "smart")
    result = approval._run_approval_gate(pattern_key="smart", description="test", display_target="echo test",
                                        autoapprove_log_prefix="test", respect_smart_mode=True)
    assert result["approved"]
    assert read_audit()[0]["mode"] == "smart"
    assert read_audit()[0]["outcome"] == "auto_approved"
    monkeypatch.setattr(approval, "_presence", lambda cb=None: (cb, False, False, False))
    monkeypatch.setattr(approval, "_unattended_contexts", lambda: [])
    result = approval._run_approval_gate(pattern_key="test", description="test", display_target="test",
                                        autoapprove_log_prefix="test")
    assert result["approved"]
    assert read_audit()[0]["mode"] == "unattended"


def test_deny_rule_audited(gate, monkeypatch):
    monkeypatch.setattr(approval, "_match_user_deny_rule", lambda command: "curl *")
    assert not approval.check_dangerous_command("curl example.test", "local")["approved"]
    entry = read_audit()[0]
    assert entry["mode"] == "deny_rule"
    assert entry["outcome"] == "blocked"


def test_reader_order_and_filter():
    append("one", "first")
    append("two", "second")
    append("one", "third")
    assert [e["command_preview"] for e in read_audit(limit=2)] == ["third", "second"]
    assert [e["command_preview"] for e in read_audit(session_key="one")] == ["third", "first"]


def test_blocked_audit_writer_cannot_delay_decisions(gate, monkeypatch):
    import threading
    assert audit.flush_approval_audit()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    def blocked(**kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(audit, "write_approval_audit", blocked)
    monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "once")
    gate()
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: (gate(), done.set()))
    worker.start()
    try:
        assert done.wait(1), "audit disk I/O blocked approval delivery"
    finally:
        release.set()
        worker.join(5)
        assert audit.flush_approval_audit()


def test_cached_approval(gate, monkeypatch):
    monkeypatch.setattr(approval, "is_approved", lambda *a: True)
    monkeypatch.setattr(approval, "_is_permanently_approved", lambda *a: False)
    assert gate()["approved"]
    assert read_audit()[0]["outcome"] == "approved_session"
