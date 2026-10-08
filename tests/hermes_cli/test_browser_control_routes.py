"""Exercise the actual Desktop registration and command channel with isolated broker state."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway import browser_control_broker as control
from hermes_cli.web_routers.browser_control import router


@pytest.fixture
def bridge(monkeypatch):
    broker = control.BrowserControlBroker()
    monkeypatch.setattr(control, "get_browser_control_broker", lambda: broker)
    monkeypatch.setattr(control, "browser_control_enabled", lambda: True)
    scope = control.ControllerScope(
        principal_id=control.local_desktop_principal("default", "session"),
        profile_id="default", session_id="session", controller_id="chrome-test",
        browser_profile_id="chrome-Default", transport_family=control.LOCAL_DESKTOP_TRANSPORT_FAMILY,
        capabilities=frozenset({"browser_snapshot"}))
    app = FastAPI()
    app.include_router(router)
    return app, broker, scope


def exchange(client, broker, scope, **changes):
    payload = dict(protocol_version=1, session_id=scope.session_id,
                   controller_id=scope.controller_id, browser_profile_id=scope.browser_profile_id,
                   capabilities=sorted(scope.capabilities))
    payload.update(changes)
    grant = broker.mint_bridge_grant(scope).value
    headers = {"X-Stardust-Browser-Control-Grant": grant}
    return client.post("/v1/browser-control/register", json=payload, headers=headers), headers, payload


def test_grant_exchange_and_real_websocket_command_result(bridge):
    app, broker, scope = bridge
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response, headers, payload = exchange(client, broker, scope)
        assert response.status_code == 201
        assert client.post("/v1/browser-control/register", json=payload, headers=headers).status_code == 401
        ticket = response.json()["ticket"]
        with client.websocket_connect("/v1/browser-control/ws", subprotocols=[
                "hermes-browser-control-v1", "hermes-browser-control-ticket." + ticket]) as ws:
            assert ws.accepted_subprotocol == "hermes-browser-control-v1"
            ws.send_json({"method": "browser.controller.heartbeat", "params": {"nonce": "live"}})
            assert ws.receive_json()["params"] == {"nonce": "live", "ok": True}
            with ThreadPoolExecutor() as pool:
                result = pool.submit(broker.dispatch, scope, action="browser_snapshot", arguments={})
                frame = ws.receive_json()
                assert frame["params"]["action"] == "browser_snapshot"
                ws.send_json({"method": "browser.controller.result", "params": {
                    "command_id": frame["params"]["command_id"], "ok": True,
                    "result": {"text": "existing tab"}}})
                assert result.result(timeout=3) == {"text": "existing tab"}


def test_registration_fails_closed_for_scope_remote_and_disabled(bridge, monkeypatch):
    app, broker, scope = bridge
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response, _, _ = exchange(client, broker, scope, session_id="foreign")
        assert response.status_code == 403
        assert client.post("/v1/browser-control/register", json={"protocol_version": 1}).status_code == 401
        monkeypatch.setattr(control, "browser_control_enabled", lambda: False)
        assert exchange(client, broker, scope)[0].status_code == 403
    monkeypatch.setattr(control, "browser_control_enabled", lambda: True)
    with TestClient(app, client=("192.0.2.1", 12345)) as client:
        assert exchange(client, broker, scope)[0].status_code == 403
    app.state.auth_required = True
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert exchange(client, broker, scope)[0].status_code == 403
