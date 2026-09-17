import json

from gateway.session_context import clear_session_vars, set_session_vars
from tools import browser_action_approval as approval


def test_snapshot_submit_button_requires_confirmation():
    risk = approval.inspect_snapshot_click(
        '- button "Submit" [e1]',
        "@e1",
        url="https://example.test/form?token=secret",
    )
    assert risk.resolved is True
    assert risk.requires_approval is True
    assert risk.role == "button"
    assert risk.name == "Submit"
    assert risk.context_sha256
    assert "?token=" not in risk.reason


def test_snapshot_navigation_link_stays_automatic():
    risk = approval.inspect_snapshot_click(
        '- link "Next" [e2]',
        "e2",
        url="https://example.test/page/1",
    )
    assert risk.resolved is True
    assert risk.requires_approval is False
    assert risk.role == "link"
    assert risk.name == "Next"


def test_agent_browser_ref_format_and_exact_ref_boundary():
    snapshot = '\n'.join([
        'button "Delete account" [ref=e10]',
        'button "Open menu" [ref=e1]',
    ])
    risk = approval.inspect_snapshot_click(snapshot, "e1")
    assert risk.resolved is True
    assert risk.requires_approval is False
    assert risk.name == "Open menu"

    destructive = approval.inspect_snapshot_click(snapshot, "e10")
    assert destructive.requires_approval is True
    assert destructive.name == "Delete account"


def test_unicode_accessible_name_is_preserved_and_classified():
    risk = approval.inspect_snapshot_click(
        '- button "提交订单" [e3]',
        "e3",
        url="https://shop.example.test/checkout",
    )
    assert risk.resolved is True
    assert risk.requires_approval is True
    assert risk.name == "提交订单"


def test_controller_structured_refs_are_classified():
    risk = approval.inspect_controller_snapshot(
        {
            "url": "https://shop.example.test/cart",
            "refs": [
                {"ref": "e3", "role": "button", "name": "Buy now"},
                {"ref": "e4", "role": "link", "name": "Continue shopping"},
            ],
        },
        "@e3",
    )
    assert risk.resolved is True
    assert risk.requires_approval is True
    assert risk.name == "Buy now"


def test_unknown_ref_blocks_personal_assistant_click(monkeypatch):
    tokens = set_session_vars(platform="", source="desktop")
    try:
        result = approval.guard_browser_click_with_probe(
            "e99",
            lambda: approval.BrowserClickRisk(False, resolved=False),
        )
    finally:
        clear_session_vars(tokens)

    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["status"] == "blocked"
    assert "Refresh the browser snapshot" in payload["error"]


def test_interactive_approval_rechecks_target_and_blocks_semantic_drift(monkeypatch):
    tokens = set_session_vars(platform="", source="desktop")
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True},
    )
    first = approval.inspect_snapshot_click(
        '- button "Send" [e5]', "e5", url="https://mail.example.test/compose"
    )
    changed = approval.inspect_snapshot_click(
        '- button "Delete" [e5]', "e5", url="https://mail.example.test/compose"
    )
    calls = []

    def probe():
        calls.append(True)
        return first if len(calls) == 1 else changed

    try:
        result = approval.guard_browser_click_with_probe("e5", probe)
    finally:
        clear_session_vars(tokens)

    payload = json.loads(result)
    assert len(calls) == 2
    assert payload["success"] is False
    assert payload["status"] == "blocked"
    assert "changed after approval" in payload["error"]


def test_interactive_approval_stable_target_allows_click(monkeypatch):
    tokens = set_session_vars(platform="", source="desktop")
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True},
    )
    stable = approval.inspect_snapshot_click(
        '- button "Publish" [e8]', "e8", url="https://example.test/post"
    )
    calls = []

    def probe():
        calls.append(True)
        return stable

    try:
        result = approval.guard_browser_click_with_probe("e8", probe)
    finally:
        clear_session_vars(tokens)

    assert result is None
    assert len(calls) == 2
