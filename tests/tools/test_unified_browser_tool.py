"""Unified Browser exercises the real session owner and existing driver seams.

No second tab process, driver state, or unsecured host fallback is introduced.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import unified_browser_tool as ub


def agent(*, platform="desktop", turn="turn-1", session="session-1"):
    return SimpleNamespace(platform=platform, _current_turn_id=turn, session_id=session)


def run(owner, action="elements", target="in_app", *, drive=None, read=None, **kwargs):
    if drive is None:
        drive = lambda payload: json.dumps({"success": True, "payload": payload})
    if read is None:
        read = lambda start=None, count=None: json.dumps({"success": True, "text": "Visible page"})
    return json.loads(ub.run_unified_browser(
        owner, {"action": action, "target": target, **kwargs},
        drive_callback=drive, read_callback=read, task_id="task-1", tool_call_id="call-1",
    ))


def test_default_in_app_uses_existing_preview_open_and_live_drive_without_second_browser(monkeypatch):
    emitted = []
    monkeypatch.setattr("tools.desktop_ui.emit", lambda name, payload: emitted.append((name, payload)) or True)
    owner = agent()

    opened = run(owner, action="open", url="example.com")
    assert opened["success"] is True
    assert opened["url"] == "https://example.com"
    assert emitted == [("preview.open", {"url": "https://example.com", "label": ""})]

    calls = []
    result = run(owner, action="click", ref="btn-sign-in", drive=lambda payload: (
        calls.append(payload) or '{"success": true, "acted": "clicked"}'
    ))
    assert result["acted"] == "clicked"
    assert calls == [{"action": "click", "ref": "btn-sign-in"}]
    assert owner._stardust_browser_turn == {"turn_id": "turn-1", "target": "in_app"}
    assert len(emitted) == 1  # Browser clicks never issue another preview.open.


def test_in_app_scroll_converts_direction_to_signed_pixels_and_reads_same_page():
    owner = agent()
    up = run(owner, action="scroll", direction="up")
    assert up["payload"]["amount"] == -500
    down = run(owner, action="scroll", direction="down", amount=123)
    assert down["payload"]["amount"] == 123
    text = run(owner, action="read")
    assert text["text"] == "Visible page"


def test_explicit_target_is_pinned_for_one_turn_and_cleared_for_next(monkeypatch):
    owner = agent()
    run(owner, action="elements")

    changed = run(owner, action="open", target="host", url="https://example.com")
    assert "Cannot switch" in changed["error"]

    owner._current_turn_id = "turn-2"
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: True)
    forwarded = []
    monkeypatch.setattr(
        "tools.browser_extension_router.routed_browser_handler",
        lambda name, args, **kw: forwarded.append((name, args, kw)) or '{"success": true}',
    )
    moved = run(owner, action="open", target="host", url="https://example.com")
    assert moved["success"] is True
    assert forwarded[0][0] == "browser_navigate"
    assert owner._stardust_browser_turn["target"] == "host"


def test_missing_host_extension_fails_closed_without_copying_or_opening_webview(monkeypatch):
    owner = agent()
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: False)
    monkeypatch.setattr(
        "tools.browser_extension_router.routed_browser_handler",
        lambda *_args, **_kw: pytest.fail("must not dispatch to legacy browser"),
    )
    failed = run(owner, action="open", target="host", url="https://example.com")
    assert "authorized host browser controller" in failed["error"]
    assert owner._stardust_browser_turn["target"] == "host"
    refused_switch = run(owner, action="open", target="in_app", url="https://example.com")
    assert "Cannot switch" in refused_switch["error"]


def test_host_actions_reuse_the_authenticated_broker_and_require_permission(monkeypatch):
    owner = agent()
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: True)
    forwarded = []
    monkeypatch.setattr(
        "tools.browser_extension_router.routed_browser_handler",
        lambda name, args, **kw: forwarded.append((name, args, kw)) or '{"success": true}',
    )
    approvals = []
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda name, reason, rule_key: approvals.append((name, rule_key)) or {"approved": True},
    )
    run(owner, action="elements", target="host")
    assert forwarded[0][0] == "browser_snapshot"
    clicked = run(owner, action="click", target="host", ref="@e2")
    assert clicked["success"] is True
    assert forwarded[1][0:2] == ("browser_click", {"ref": "@e2"})
    assert forwarded[1][2] == {
        "fallback": forwarded[1][2]["fallback"],
        "task_id": "task-1", "session_id": "session-1", "tool_call_id": "call-1",
    }
    assert approvals[0][0] == "browser"

    typed = run(owner, action="type", target="host", ref="@e2", text="some non-secret")
    assert typed["success"] is True
    assert forwarded[2][0:2] == ("browser_type", {"ref": "@e2", "text": "some non-secret"})


def test_denied_host_input_does_not_fall_back_to_unrelated_page(monkeypatch):
    owner = agent()
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: True)
    monkeypatch.setattr(
        "tools.browser_extension_router.routed_browser_handler",
        lambda *_args, **_kw: pytest.fail("input was denied"),
    )
    monkeypatch.setattr("tools.approval.request_tool_approval", lambda *a, **kw: {"approved": False})
    refused = run(owner, action="click", target="host", ref="@e2")
    assert "error" in refused
    assert owner._stardust_browser_turn["target"] == "host"


def test_host_errors_pin_selected_target_and_do_not_retry_another_driver(monkeypatch):
    owner = agent()
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: True)
    monkeypatch.setattr(
        "tools.browser_extension_router.routed_browser_handler",
        lambda *a, **kw: '{"ok": false, "error": "controller offline"}',
    )
    failed = run(owner, action="elements", target="host")
    assert failed["ok"] is False
    assert owner._stardust_browser_turn["target"] == "host"
    attempted_fallback = run(owner, action="elements", target="in_app")
    assert "Cannot switch" in attempted_fallback["error"]


@pytest.mark.parametrize("raw", [
    "file:///private/secret", "javascript:alert(1)",
    "https:///path-only", "https://user:password@example.com",
])
def test_rejects_non_web_or_credentialed_urls_before_dispatch(raw, monkeypatch):
    monkeypatch.setattr("tools.desktop_ui.emit", lambda *_: pytest.fail("invalid URL reached GUI"))
    result = run(agent(), action="open", url=raw)
    assert "error" in result


def test_unsupported_host_actions_and_wrong_ref_types_fail_closed(monkeypatch):
    owner = agent()
    unsupported = run(owner, action="hover", target="host", ref="@e2")
    assert "error" in unsupported
    assert "does not support" in unsupported["error"] or "authorized host" in unsupported["error"]
    selector = run(owner, action="click", target="host", selector="#pay")
    assert "error" in selector
    submit = run(owner, action="type", target="host", ref="@e1", text="x", submit=True)
    assert "error" in submit


def test_status_reports_real_availability_without_claiming_connection(monkeypatch):
    owner = agent()
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: False)
    status = run(owner, action="status", target="host")
    assert status == {
        "success": True, "active_target": None,
        "requested_target": "host", "available": False,
    }
    assert run(owner, action="status")["available"] is True


def test_non_desktop_and_missing_turn_have_no_browser_authority():
    assert "error" in run(agent(platform="cli"), action="elements")
    assert "error" in run(agent(turn=""), action="elements")


def test_browser_registry_handler_cannot_bypass_agent_bound_gui_callbacks():
    from tools.registry import discover_builtin_tools, registry
    discover_builtin_tools()
    definition = registry.get_definitions({"browser"}, quiet=True)
    assert definition[0]["function"]["name"] == "browser"
    assert "target" in definition[0]["function"]["parameters"]["required"]
    result = registry.dispatch("browser", {"action": "open", "target": "in_app", "url": "https://example.com"})
    assert "error" in json.loads(result)


# ── connect: the agent pairs the user's Chrome itself (owner field case 2026-10-12) ──────────────────────


def _connect_seams(monkeypatch, *, attached=False, approved=True, desktop=True):
    seen = {"approvals": [], "emitted": []}
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: attached)

    def approve(tool, reason, **kw):
        seen["approvals"].append((tool, reason, kw.get("rule_key")))
        return {"approved": approved, "message": None if approved else "User declined."}

    monkeypatch.setattr("tools.approval.request_tool_approval", approve)
    monkeypatch.setattr(
        "tools.desktop_ui.emit", lambda name, payload: desktop and (seen["emitted"].append((name, payload)) or True),
    )
    return seen


def test_connect_asks_once_then_has_the_desktop_pair_chrome_and_keeps_the_setup_with_the_agent(monkeypatch):
    seen = _connect_seams(monkeypatch)

    result = run(agent(), action="connect", target="host", chrome_profile="Profile 2")

    assert result["requested"] is True
    assert seen["approvals"] == [("browser", seen["approvals"][0][1], "browser_host_connect")]
    assert seen["emitted"] == [("browser.host.connect", {"chrome_profile_dir": "Profile 2"})]
    # The agent is told to install and approve the extension itself, not to hand the setup back.
    assert "computer_use" in result["next"] and ub.PLAYWRIGHT_EXTENSION_URL in result["next"]
    assert "status" in result["next"]


def test_connect_approval_reason_uses_the_chinese_catalog(monkeypatch):
    from agent import i18n

    monkeypatch.setenv("STARDUST_LANGUAGE", "zh")
    i18n.reset_language_cache()
    try:
        seen = _connect_seams(monkeypatch)

        run(agent(), action="connect", target="host")

        assert seen["approvals"] == [(
            "browser", "让这个对话连接你自己的 Chrome，包括你已登录的网站", "browser_host_connect",
        )]
    finally:
        i18n.reset_language_cache()


def test_declined_connect_never_reaches_the_desktop(monkeypatch):
    seen = _connect_seams(monkeypatch, approved=False)

    result = run(agent(), action="connect", target="host")

    assert "declined" in result["error"]
    assert seen["emitted"] == []


def test_connect_on_an_attached_controller_reports_connected_without_asking(monkeypatch):
    seen = _connect_seams(monkeypatch, attached=True)

    assert run(agent(), action="connect", target="host") == {"success": True, "connected": True}
    assert seen["approvals"] == [] and seen["emitted"] == []


def test_connect_needs_the_desktop_app_and_the_host_target(monkeypatch):
    _connect_seams(monkeypatch, desktop=False)

    assert "desktop app" in run(agent(), action="connect", target="host")["error"]
    assert "target=host" in run(agent(turn="turn-2"), action="connect", target="in_app")["error"]


def test_a_missing_controller_points_the_agent_at_connect(monkeypatch):
    monkeypatch.setattr("tools.browser_extension_router.extension_controller_available", lambda *_: False)

    failed = run(agent(), action="open", target="host", url="https://example.com")

    assert "browser(action='connect', target='host')" in failed["error"]
