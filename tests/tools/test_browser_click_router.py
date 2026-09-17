import json

from gateway.session_context import clear_session_vars, set_session_vars
from tools.browser_extension_router import route_browser_tool


class Broker:
    def __init__(self, *, snapshot=None, snapshot_capable=True):
        self.snapshot = snapshot
        self.snapshot_capable = snapshot_capable
        self.calls = []

    def scope_for_session(self, **identity):
        self.calls.append(("scope", identity))
        return "scope-1"

    def lane_registered(self, **identity):
        return True

    def select(self, scope, action):
        self.calls.append(("select", scope, action))
        if action == "browser_snapshot" and not self.snapshot_capable:
            return None
        return "controller-1"

    def dispatch(self, scope, *, action, arguments, tool_call_id=""):
        self.calls.append(("dispatch", scope, action, arguments, tool_call_id))
        if action == "browser_snapshot":
            return self.snapshot
        return {"ok": True, "clicked": arguments.get("ref")}


def _route(broker, *, fallback=None, tool_call_id="call-1"):
    return route_browser_tool(
        "browser_click",
        {"ref": "@e1"},
        fallback=fallback or (lambda: "legacy-click"),
        broker=broker,
        enabled=True,
        session_id="session-1",
        task_id="task-1",
        principal_id="principal-1",
        transport_family="local-api",
        tool_call_id=tool_call_id,
    )


def test_legacy_click_guard_blocks_before_fallback(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "tools.browser_action_approval.guard_browser_click",
        lambda ref, task_id: '{"success": false, "status": "blocked"}',
    )

    result = route_browser_tool(
        "browser_click",
        {"ref": "@e1"},
        fallback=lambda: seen.append("fallback") or "legacy-click",
        broker=Broker(),
        enabled=False,
        task_id="task-1",
    )

    assert json.loads(result)["status"] == "blocked"
    assert seen == []


def test_extension_consequential_click_preflights_rechecks_then_clicks(monkeypatch):
    from hermes_cli import assistant_permissions

    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)
    monkeypatch.setattr(assistant_permissions, "_durable_worker_confirmation_active", lambda: False)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True},
    )
    broker = Broker(snapshot={
        "url": "https://example.test/compose",
        "snapshot": '- button "Send" [e1]',
    })

    result = json.loads(_route(broker))

    assert result == {"ok": True, "clicked": "@e1"}
    dispatches = [call for call in broker.calls if call[0] == "dispatch"]
    assert [call[2] for call in dispatches] == [
        "browser_snapshot", "browser_snapshot", "browser_click"
    ]
    assert dispatches[0][4].endswith(":stardust-preflight:1")
    assert dispatches[1][4].endswith(":stardust-preflight:2")
    assert dispatches[2][4] == "call-1"


def test_extension_normal_navigation_click_needs_no_approval(monkeypatch):
    from hermes_cli import assistant_permissions

    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)
    broker = Broker(snapshot={
        "url": "https://example.test/page/1",
        "snapshot": '- link "Next" [e1]',
    })

    result = json.loads(_route(broker))

    assert result == {"ok": True, "clicked": "@e1"}
    dispatches = [call for call in broker.calls if call[0] == "dispatch"]
    assert [call[2] for call in dispatches] == ["browser_snapshot", "browser_click"]


def test_extension_click_without_snapshot_capability_fails_closed(monkeypatch):
    from hermes_cli import assistant_permissions

    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)
    broker = Broker(snapshot_capable=False)

    result = json.loads(_route(broker))

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert "Refresh the browser snapshot" in result["error"]
    assert not any(call[0] == "dispatch" and call[2] == "browser_click" for call in broker.calls)


def test_non_assistant_extension_click_preserves_direct_dispatch(monkeypatch):
    from hermes_cli import assistant_permissions

    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: False)
    broker = Broker(snapshot_capable=False)

    result = json.loads(_route(broker))

    assert result == {"ok": True, "clicked": "@e1"}
    dispatches = [call for call in broker.calls if call[0] == "dispatch"]
    assert [call[2] for call in dispatches] == ["browser_click"]
