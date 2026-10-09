"""Standing grants confer only explicit, current profile-local authority."""
import importlib

import pytest

from tools import approval, approval_grants as grants
from tools.approval_audit import read_approval_audit


@pytest.fixture(autouse=True)
def prompt(monkeypatch):
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval, "is_approved", lambda *a: False)
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    calls = []
    def deny(*a, **kw):
        calls.append(a)
        return "deny"
    monkeypatch.setattr(approval, "prompt_dangerous_approval", deny)
    return calls


def test_in_scope_audited_without_prompt(prompt):
    grant = grants.add_grant("send_message", "telegram:123")
    assert approval.request_tool_approval("send_message", "send", target="telegram:123")["approved"]
    assert not prompt
    entry = read_approval_audit()[0]
    assert entry["outcome"] == "grant"
    assert entry["pattern_key"] == "grant:" + grant["id"]


@pytest.mark.parametrize("kind,target,amount,expiry", [
    ("send_message", "telegram:456", None, None),
    ("purchase", "shop", 11, None),
    ("purchase", "shop", None, None),
    ("purchase", "shop", 5, "2000-01-01T00:00:00Z"),
])
def test_out_of_scope_prompts(prompt, kind, target, amount, expiry):
    grants.add_grant(kind, "telegram:123" if kind == "send_message" else "shop",
                     max_amount=10 if kind == "purchase" else None, expires_at=expiry)
    assert not approval.request_tool_approval(kind, "act", target=target, amount=amount)["approved"]
    assert len(prompt) == 1


def test_amount_boundary_and_revoke_reload(prompt):
    grant = grants.add_grant("purchase", "shop", max_amount=10)
    importlib.reload(grants)
    assert grants.list_grants() == [grant]
    assert approval.request_tool_approval("purchase", "buy", target="shop", amount=10)["approved"]
    assert not prompt
    assert grants.revoke_grant(grant["id"])
    assert not grants.revoke_grant(grant["id"])
    importlib.reload(grants)
    assert grants.list_grants() == []
    assert not approval.request_tool_approval("purchase", "buy", target="shop", amount=10)["approved"]
    assert prompt


def test_profile_isolation(monkeypatch, tmp_path):
    grants.add_grant("send_message", "telegram:123")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other"))
    assert not grants.list_grants()
    assert grants.matching_grant("send_message", "telegram:123") is None


def test_command_grant_keeps_floors(prompt):
    grants.add_grant("command_pattern", "rm -rf *")
    assert approval.check_dangerous_command("rm -rf ./scratch", "local")["approved"]
    assert not prompt
    assert not approval.check_dangerous_command("rm -rf /", "local")["approved"]


@pytest.mark.parametrize("kwargs", [{"max_amount": -1}, {"max_amount": float("nan")},
                                    {"expires_at": "2027-01-01"}])
def test_invalid_grants_rejected(kwargs):
    with pytest.raises(ValueError):
        grants.add_grant("purchase", "shop", **kwargs)
    assert not grants.list_grants()


def test_send_entry_point_checks_grant(monkeypatch, prompt):
    from tools import send_message_tool as sender
    calls = []
    monkeypatch.setattr(sender, "_handle_send", lambda args: calls.append(args) or "sent")
    grants.add_grant("send_message", "telegram:123")
    assert sender.send_message_tool({"target": "telegram:123", "message": "hello"}, require_approval=True) == "sent"
    assert "denied" in sender.send_message_tool({"target": "telegram:456", "message": "hello"}, require_approval=True).lower()
    assert len(calls) == 1
    assert len(prompt) == 1


def test_prompt_cache_key_tracks_scope(prompt):
    first = approval.request_tool_approval("purchase", "buy", rule_key="checkout",
                                           target="shop", amount=10)
    other = approval.request_tool_approval("purchase", "buy", rule_key="checkout",
                                           target="shop", amount=20)
    recipient = approval.request_tool_approval("purchase", "buy", rule_key="checkout",
                                               target="other", amount=10)
    assert len({first["pattern_key"], other["pattern_key"], recipient["pattern_key"]}) == 3


def test_corrupt_store_confers_no_authority():
    from hermes_constants import get_hermes_home
    path = get_hermes_home() / "approval_grants.json"
    path.write_text('[null, {"action_kind":"purchase"}]')
    assert grants.matching_grant("purchase", "shop", 1) is None
    path.write_text('not json')
    assert grants.matching_grant("purchase", "shop", 1) is None
