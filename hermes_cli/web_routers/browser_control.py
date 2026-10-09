"""Loopback Desktop bridge ingress; only session-bound single-use grants are accepted."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from gateway import browser_control_broker as control
from hermes_cli.web_server_profiles import _config_profile_scope

router = APIRouter()
PROTOCOL = "hermes-browser-control-v1"
TICKET_PREFIX = "hermes-browser-control-ticket."


def _local(peer, app) -> bool:
    return (peer is not None and peer.host in {"127.0.0.1", "::1"}
            and not getattr(app.state, "auth_required", False))


@router.post("/v1/browser-control/register")
async def register(request: Request):
    if not _local(request.client, request.app):
        raise HTTPException(403, "local Desktop browser control is unavailable")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid registration") from None
    if not isinstance(payload, dict) or not control.browser_control_protocol_supported(payload.get("protocol_version")):
        raise HTTPException(400, "unsupported browser-control protocol")
    broker = control.get_browser_control_broker()
    # The grant supplies every identity field; the controller may only assert them.
    grant = request.headers.get("X-Stardust-Browser-Control-Grant", "")
    try:
        scope = broker.consume_bridge_grant(grant)
    except control.LaunchGrantInvalid:
        raise HTTPException(401, "invalid browser launch grant") from None
    with _config_profile_scope(scope.profile_id):
        if not control.browser_control_enabled():
            raise HTTPException(403, "browser control is disabled for this profile")
    if (scope.transport_family != control.LOCAL_DESKTOP_TRANSPORT_FAMILY
            or any(payload.get(key) != getattr(scope, key)
                   for key in ("session_id", "controller_id", "browser_profile_id"))
            or control.filter_browser_control_capabilities(payload.get("capabilities")) != scope.capabilities):
        raise HTTPException(403, "browser launch scope mismatch")
    ticket = broker.mint_ticket(scope)
    data = asdict(scope)
    data["capabilities"] = sorted(scope.capabilities)
    return JSONResponse({"protocol_version": control.BROWSER_CONTROL_PROTOCOL_VERSION,
                         "ticket": ticket.value, "ws_path": "/v1/browser-control/ws",
                         "scope": data}, status_code=201)


@router.websocket("/v1/browser-control/ws")
async def controller_ws(ws: WebSocket):
    if not _local(ws.client, ws.app):
        await ws.close(code=4403)
        return
    protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
    tickets = [p[len(TICKET_PREFIX):] for p in protocols if p.startswith(TICKET_PREFIX)]
    broker = control.get_browser_control_broker()
    try:
        if PROTOCOL not in protocols or len(tickets) != 1:
            raise control.ControllerTicketInvalid("ticket required")
        scope = broker.consume_ticket(tickets[0])
        if scope.transport_family != control.LOCAL_DESKTOP_TRANSPORT_FAMILY:
            raise control.ControllerTicketInvalid("local controller required")
        with _config_profile_scope(scope.profile_id):
            if not control.browser_control_enabled():
                raise control.ControllerTicketInvalid("browser control disabled")
    except control.ControllerTicketInvalid:
        await ws.close(code=4401)
        return
    await ws.accept(subprotocol=PROTOCOL)
    loop = asyncio.get_running_loop()

    def send(frame):
        # Broker dispatch is synchronous and runs off-loop; report write failures to it.
        asyncio.run_coroutine_threadsafe(ws.send_json(frame), loop).result(timeout=10)

    try:
        await asyncio.to_thread(broker.attach, scope, send, owner=ws)
    except control.ControllerRejected:
        await ws.close(code=4403)
        return
    try:
        while True:
            frame = await ws.receive_json()
            if not isinstance(frame, dict) or not isinstance(frame.get("params"), dict):
                continue
            if not broker.is_owner(scope, ws):
                break
            params = frame["params"]
            method = frame.get("method")
            if method == "browser.controller.heartbeat":
                nonce = params.get("nonce")
                if isinstance(nonce, str) and 0 < len(nonce) <= 128:
                    await ws.send_json({"method": method, "params": {"nonce": nonce, "ok": True}})
            elif method == "browser.controller.result":
                command_id = params.get("command_id")
                if isinstance(command_id, str) and command_id:
                    ok = params.get("ok") is True
                    await asyncio.to_thread(broker.complete, command_id, scope=scope, ok=ok,
                                            result=params.get("result") if ok else params.get("error"))
            elif method == "browser.controller.detach":
                await asyncio.to_thread(broker.detach, scope, owner=ws, notify_controller=False)
                break
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        await asyncio.to_thread(broker.disconnect, scope, owner=ws)
