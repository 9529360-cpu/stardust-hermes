"""Stored grants are management records, not automatic security authority."""
import importlib
import threading

import pytest
from tools import approval, approval_grants as grants


@pytest.fixture(autouse=True)
def prompt(monkeypatch):
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval, "is_approved", lambda *a: False)
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    calls = []
    monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **kw: calls.append(a) or "deny")
    return calls


def test_records_persist_revoke_but_never_autoapprove(prompt):
    grant = grants.add_grant("send_message", "telegram:123")
    importlib.reload(grants)
    assert grants.list_grants() == [grant]
    assert not approval.request_tool_approval("send_message", "send", target="telegram:123")["approved"]
    assert prompt
    assert grants.revoke_grant(grant["id"])
    assert not grants.revoke_grant(grant["id"])
    assert grants.list_grants() == []


def test_grant_lock_never_delays_prompt(prompt, monkeypatch):
    # Model a grant-management call parked on filesystem I/O while holding its lock.
    entered, release = threading.Event(), threading.Event()
    def hold():
        with grants._lock:
            entered.set()
            assert release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(2)
    done = threading.Event()
    def check():
        try:
            assert not approval.request_tool_approval("send_message", "send", target="alias")["approved"]
        finally:
            done.set()
    worker = threading.Thread(target=check)
    worker.start()
    try:
        assert done.wait(1), "approval prompt blocked on grant storage"
    finally:
        release.set()
        thread.join(5)
        worker.join(5)
    assert prompt


@pytest.mark.parametrize("kind,maximum", [("purchase", None), ("payment", 10),
                                          ("send_message", 10), ("unknown", None)])
def test_unsupported_monetary_grants_zero_write(kind, maximum, tmp_path):
    with pytest.raises(ValueError):
        grants.add_grant(kind, "shop", max_amount=maximum)
    assert not (tmp_path / "approval_grants.json").exists()
    assert grants.matching_grant(kind, "shop", 1) is None


def test_send_alias_grant_and_cached_choice_never_bypass_prompt(monkeypatch, prompt):
    from tools import send_message_tool as sender
    grants.add_grant("send_message", "alias")
    monkeypatch.setattr(approval, "is_approved", lambda *a: True)
    calls = []
    monkeypatch.setattr(sender, "_handle_send", lambda args: calls.append(args) or "sent")
    reply = sender.send_message_tool({"target": "alias", "message": "hello"}, require_approval=True)
    assert "denied" in reply.lower()
    assert prompt and not calls


def test_send_always_is_not_persisted(monkeypatch):
    from tools import send_message_tool as sender
    monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **kw: "always")
    monkeypatch.setattr(approval, "_persist_choice", lambda *a: pytest.fail("alias authority persisted"))
    monkeypatch.setattr(sender, "_handle_send", lambda args: "sent")
    for _ in range(2):
        assert sender.send_message_tool({"target": "alias", "message": "hi"}, require_approval=True) == "sent"


def test_command_grant_keeps_prompt_and_floor(prompt):
    grants.add_grant("command_pattern", "rm -rf *")
    assert not approval.check_dangerous_command("rm -rf ./scratch", "local")["approved"]
    assert prompt
    assert not approval.check_dangerous_command("rm -rf /", "local")["approved"]


def test_profile_isolation(monkeypatch, tmp_path):
    grants.add_grant("send_message", "telegram:123")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other"))
    assert grants.list_grants() == []


def test_corrupt_legacy_purchase_confers_no_authority(tmp_path):
    from hermes_constants import get_hermes_home
    path = get_hermes_home() / "approval_grants.json"
    path.write_text('[{"action_kind":"purchase","target":"shop","max_amount":10}]')
    assert grants.matching_grant("purchase", "shop", 1) is None
