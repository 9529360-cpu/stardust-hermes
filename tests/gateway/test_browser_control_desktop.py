from types import SimpleNamespace

import pytest

from gateway import browser_control_broker as control
from gateway.session_context import clear_session_vars, get_session_env
from hermes_cli import web_server, web_server_chat
from tools.browser_extension_router import _bound_identity
from tui_gateway import server


def test_local_session_token_mints_bridge_grant_and_binds_ui_session(monkeypatch):
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(control, "browser_control_enabled", lambda: True)
    broker = control.BrowserControlBroker()
    monkeypatch.setattr(control, "get_browser_control_broker", lambda: broker)
    ws = SimpleNamespace(query_params={"token": web_server._SESSION_TOKEN},
                         client=SimpleNamespace(host="127.0.0.1"))
    assert web_server_chat._ws_auth_reason(ws) == (None, "token")
    transport = SimpleNamespace(auth_identity=ws._hermes_auth_identity, write=lambda frame: True)
    sid = "desktop-ui-session"
    server._sessions[sid] = {"transport": transport, "session_key": "durable-key",
                             "profile_home": None, "agent": SimpleNamespace(session_id="durable-id")}
    tokens = []
    try:
        reply = server.dispatch({"jsonrpc": "2.0", "id": 1,
            "method": "browser.controller.bridge_prepare", "params": {
                "session_id": sid, "browser_profile_id": "chrome-Default",
                "protocol_version": 1, "capabilities": ["browser_snapshot"]}}, transport)
        assert "error" not in reply, reply
        launch = reply["result"]["launch_context"]
        scope = broker.consume_bridge_grant(launch["grant"])
        tokens = server._set_session_context("durable-key")
        assert get_session_env("HERMES_SESSION_ID") == "durable-id"
        assert _bound_identity() == (sid, scope.principal_id, scope.transport_family)
        def send(frame):
            broker.complete(frame['params']['command_id'], scope=scope, ok=True,
                            result={'success': True, 'text': 'existing tab'})
        broker.attach(scope, send)
        from tools.unified_browser_tool import run_unified_browser
        owner = SimpleNamespace(platform='desktop', session_id='durable-id', _current_turn_id='turn')
        assert 'existing tab' in run_unified_browser(owner, {'target': 'host', 'action': 'read'},
                                                    drive_callback=None, read_callback=None)
        status = server.dispatch({"jsonrpc": "2.0", "id": 3,
            "method": "browser.controller.bridge_status", "params": {"session_id": sid}}, transport)
        assert status["result"]["status"] == "connected"
        broker.disconnect(scope)
        offline = server.dispatch({"jsonrpc": "2.0", "id": 5,
            "method": "browser.controller.bridge_status", "params": {"session_id": sid}}, transport)
        assert offline["result"]["status"] == "error"
        revoked = server.dispatch({"jsonrpc": "2.0", "id": 4,
            "method": "browser.controller.bridge_revoke", "params": {
                "session_id": sid, "controller_id": scope.controller_id}}, transport)
        assert revoked["result"]["status"] == "inactive"
        foreign = server.dispatch({"jsonrpc": "2.0", "id": 2,
            "method": "browser.controller.bridge_prepare", "params": {
                "session_id": sid, "protocol_version": 1}},
            SimpleNamespace(auth_identity=ws._hermes_auth_identity))
        assert foreign["error"]["code"] == 4403
    finally:
        clear_session_vars(tokens)
        server._sessions.pop(sid, None)


def test_remote_token_never_becomes_local_desktop_identity(monkeypatch):
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    ws = SimpleNamespace(query_params={"token": web_server._SESSION_TOKEN},
                         client=SimpleNamespace(host="192.0.2.1"))
    assert web_server_chat._ws_auth_reason(ws) == (None, "token")
    assert not hasattr(ws, "_hermes_auth_identity")


def test_revoke_before_attach_invalidates_prepared_grant_and_consumed_ticket(monkeypatch):
    broker = control.BrowserControlBroker()
    monkeypatch.setattr(control, "get_browser_control_broker", lambda: broker)
    sid = 'pending-desktop'
    transport = SimpleNamespace(auth_identity={'user_id': 'local-desktop', 'provider': 'loopback-session'})
    server._sessions[sid] = {'transport': transport, 'profile_home': None}
    scopes = [control.ControllerScope(
        principal_id=control.local_desktop_principal('default', sid), profile_id='default',
        session_id=sid, controller_id=controller, browser_profile_id='chrome-Default',
        transport_family='local-api', capabilities=frozenset({'browser_snapshot'}))
        for controller in ('prepared', 'registered')]
    prepared = broker.mint_bridge_grant(scopes[0])
    registered = broker.mint_bridge_grant(scopes[1])
    scope = broker.consume_bridge_grant(registered.value)
    ticket = broker.mint_ticket(scope)
    consumed = broker.consume_ticket(ticket.value)
    try:
        revoked = server.dispatch({'jsonrpc': '2.0', 'id': 1,
            'method': 'browser.controller.bridge_revoke', 'params': {'session_id': sid}}, transport)
        assert revoked['result']['status'] == 'inactive'
        with pytest.raises(control.LaunchGrantInvalid):
            broker.consume_bridge_grant(prepared.value)
        with pytest.raises(control.ControllerRejected, match='revoked'):
            broker.attach(consumed, lambda frame: None)
        fresh = broker.mint_bridge_grant(scopes[0])
        broker.consume_bridge_grant(fresh.value)
        broker.attach(scopes[0], lambda frame: None)
        assert broker.select(scopes[0], 'browser_snapshot') is not None
    finally:
        server._sessions.pop(sid, None)
