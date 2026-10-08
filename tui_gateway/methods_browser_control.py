"""Browser controller registration and result routing for the dashboard.

The controller extension registers over the authenticated ``/api/ws`` gateway. Everything
binds to the SERVER-MINTED identity (``WSTransport.auth_identity``, stamped from the single-use
ticket); a client-supplied ``principal_id`` is ignored and replaced by a digest of it. Broker
frames are re-enveloped as Gateway ``event`` frames; ``result`` resolves a command only when the
request arrives on a transport ATTACHED to the session and that transport is the broker-recorded
owner of the exact attached scope (the broker's exact-scope ``complete`` is the backstop).
Capabilities come from the broker's explicit allowlist (no raw CDP/eval/uploads). Bodies are
rebound onto server.py's globals (bind_module publishes this module's helpers too), which is how
the session gate reaches ``_session_transport_contains`` with no import of its own.
"""

from __future__ import annotations

import hashlib
import logging

from gateway.browser_control_broker import (
    BROWSER_CONTROL_PROTOCOL_VERSION,
    LOCAL_DESKTOP_TRANSPORT_FAMILY,
    ControllerScope,
    local_desktop_principal,
)
from hermes_cli.dashboard_auth.ws_tickets import (
    INTERNAL_PROVIDER as _INTERNAL_PROVIDER, INTERNAL_USER_ID as _INTERNAL_USER_ID)

from .method_ctx import HandlerRegistry, bind_module
from .session_transports import _session_transport_contains
from .transport import current_transport

logger = logging.getLogger(__name__)

_registry = HandlerRegistry()
method = _registry.method

# Transport family stamped into every scope attached here; the broker treats it as an
# identity field, so an API transport can never address a dashboard controller.
_CLOUD_TRANSPORT_FAMILY = "cloud-ticket-ws"
_ERR_FORBIDDEN = 4403  # identity / session / flag denials
_IDENTITY_REQUIRED = "authenticated controller identity required"
_NOT_OWNED = "controller is not owned by this transport"
_NO_CONTROLLER = "no controller registered for this session"


def _is_authenticated_identity(identity: object) -> bool:
    """True for a server-minted, non-internal ``{user_id, provider}`` identity."""
    if not isinstance(identity, dict):
        return False
    if identity.get("provider") == "loopback-session":
        return False
    user_id, provider = identity.get("user_id"), identity.get("provider")
    if not isinstance(user_id, str) or not user_id.strip():
        return False
    if not isinstance(provider, str) or not provider.strip():
        return False
    return not (user_id == _INTERNAL_USER_ID and provider == _INTERNAL_PROVIDER)


def _principal_digest(identity: dict) -> str:
    """Server-derived principal id: stable per user, unspoofable without the minted identity."""
    raw = f"{identity.get('provider')}\x00{identity.get('user_id')}"
    return f"principal:dashboard:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _broker_event_writer(transport: object, session_id: str):
    """Broker send callback: re-envelope ``{method, params}`` as a Gateway ``event`` frame
    (``type`` = method, ``payload`` = params, plus the owning ``session_id``)."""

    def send(frame: dict) -> None:
        try:
            accepted = transport.write({
                "jsonrpc": "2.0", "method": "event",
                "params": {
                    "type": frame.get("method"), "session_id": session_id,
                    "payload": frame.get("params"),
                }})
        except Exception:
            logger.exception(
                "browser controller event write failed session=%s frame=%s",
                session_id, frame.get("method"),
            )
            raise
        if accepted is False:
            raise ConnectionError("browser controller event write failed")

    return send


def _controller_method(
    name: str, *, identity_message: str = _IDENTITY_REQUIRED, lookup_scope: bool = True,
    missing_scope_message: str = _NO_CONTROLLER, precheck=None, desktop_bridge=False):
    """Register a handler behind the shared fail-closed (4403) controller gates.

    Order: ``precheck(rid, params)`` (may return an error envelope) → caller holds a
    server-authenticated, non-internal identity → the named session exists and the caller is
    ATTACHED to it, directly or through the ``FanoutTransport`` a mirrored session holds in its
    slot → when ``lookup_scope``, a scope is attached for this session/principal/family and the
    caller owns it. Then
    ``fn(rid, params, transport, identity, session_id, broker, scope, session)`` runs.
    """

    def dec(fn):
        def handler(rid, params: dict) -> dict:
            from gateway import browser_control_broker

            if precheck is not None and not desktop_bridge:
                denied = precheck(rid, params)
                if denied is not None:
                    return denied
            transport = current_transport()
            identity = getattr(transport, "auth_identity", None)
            if desktop_bridge and identity != {"user_id": "local-desktop", "provider": "loopback-session"}:
                return _err(rid, _ERR_FORBIDDEN, "local Desktop connection required")
            if not desktop_bridge and isinstance(identity, dict) and identity.get("provider") == "loopback-session":
                return _err(rid, _ERR_FORBIDDEN, identity_message)
            if not desktop_bridge and not _is_authenticated_identity(identity):
                return _err(rid, _ERR_FORBIDDEN, identity_message)
            session_id = str(params.get("session_id") or "")
            with _sessions_lock:
                session = _sessions.get(session_id)
                # Membership, not slot identity: a mirrored session holds a FanoutTransport, which is
                # identical to no peer's transport, so slot identity would refuse every client here — the
                # peer that registered the controller included. The broker's is_owner check below still
                # keys on the transport that attached the scope.
                if not _session_transport_contains(session, transport):
                    return _err(rid, _ERR_FORBIDDEN, "session is not owned by this transport")
            broker = browser_control_broker.get_browser_control_broker()
            if desktop_bridge:
                with _session_profile_runtime_scope(session):
                    if precheck is not None:
                        denied = precheck(rid, params)
                        if denied is not None:
                            return denied
                    return fn(rid, params, transport, identity, session_id, broker, None, session)
            scope = None
            if lookup_scope:
                scope = broker.scope_for_session(
                    session_id=session_id, principal_id=_principal_digest(identity),
                    transport_family=_CLOUD_TRANSPORT_FAMILY)
                if scope is None:
                    return _err(rid, _ERR_FORBIDDEN, missing_scope_message)
                # Defense in depth: the broker's exact-scope ops already reject foreign
                # scopes; the owner check makes the same-transport rule explicit here too.
                if not broker.is_owner(scope, transport):
                    return _err(rid, _ERR_FORBIDDEN, _NOT_OWNED)
            return fn(rid, params, transport, identity, session_id, broker, scope, session)

        handler.__doc__ = fn.__doc__
        return method(name)(handler)

    return dec


def _register_precheck(rid, params: dict):
    from gateway import browser_control_broker

    if not browser_control_broker.browser_control_enabled():
        return _err(rid, _ERR_FORBIDDEN, "browser.extension_control.enabled is not set")
    broker_mod = browser_control_broker
    if not broker_mod.browser_control_protocol_supported(params.get("protocol_version")):
        expected = broker_mod.BROWSER_CONTROL_PROTOCOL_VERSION
        return _err(
            rid, _ERR_FORBIDDEN,
            f"unsupported browser-control protocol version; expected {expected}",
        )
    return None


@_controller_method(
    "browser.controller.register",
    identity_message="browser.controller.register requires an authenticated non-internal identity",
    lookup_scope=False, precheck=_register_precheck)
def _(rid, params: dict, transport, identity, session_id, broker, _scope, session) -> dict:
    """Attach this connection as the browser controller for one session; fails closed (4403) unless
    the flag is on, the protocol version is supported, the gates pass and a capability survives."""
    from gateway import browser_control_broker

    controller_id = str(params.get("controller_id") or "").strip()
    browser_profile_id = str(params.get("browser_profile_id") or "").strip()
    profile_id = _session_profile(session)
    if not controller_id or not browser_profile_id or not profile_id:
        return _err(
            rid, _ERR_FORBIDDEN,
            "controller_id, browser_profile_id, and server session profile are required",
        )
    capabilities = browser_control_broker.filter_browser_control_capabilities(
        params.get("capabilities")
    )
    if not capabilities:
        return _err(rid, _ERR_FORBIDDEN, "no permitted controller capabilities requested")
    scope = browser_control_broker.ControllerScope(
        principal_id=_principal_digest(identity), profile_id=profile_id, session_id=session_id,
        controller_id=controller_id, browser_profile_id=browser_profile_id,
        transport_family=_CLOUD_TRANSPORT_FAMILY, capabilities=capabilities)
    broker.attach(scope, _broker_event_writer(transport, session_id), owner=transport)
    return _ok(rid, {
        "scope": {
            "principal_id": scope.principal_id, "profile_id": scope.profile_id,
            "session_id": scope.session_id, "controller_id": scope.controller_id,
            "browser_profile_id": scope.browser_profile_id,
            "transport_family": scope.transport_family,
            "capabilities": sorted(scope.capabilities)}})


@_controller_method("browser.controller.result")
def _(rid, params: dict, _transport, _identity, _session_id, broker, scope, _session) -> dict:
    """Deliver one command result to the broker; ``accepted`` is False for unknown / resolved /
    cancelled command ids (the broker's idempotent answer, surfaced verbatim)."""
    command_id = str(params.get("command_id") or "")
    if not command_id:
        return _err(rid, _ERR_FORBIDDEN, "command_id required")
    ok = params.get("ok") is True
    accepted = broker.complete(
        command_id, scope=scope, ok=ok, result=params.get("result") if ok else params.get("error"))
    return _ok(rid, {"accepted": accepted})


@_controller_method("browser.controller.heartbeat")
def _(rid, params: dict, *_gate) -> dict:
    """Acknowledge a heartbeat only for this transport's own attached controller.

    The session gate admits any client attached to the session, including a fan-out peer; the
    broker's ``is_owner`` check then narrows the answer to the transport that actually registered
    the controller."""
    return _ok(rid, {"ok": True})


@_controller_method("browser.controller.detach", missing_scope_message=_NOT_OWNED)
def _(rid, params: dict, transport, _identity, _session_id, broker, scope, _session) -> dict:
    """Hard-detach only the controller owned by this authenticated transport."""
    broker.detach(scope, owner=transport, notify_controller=False)
    return _ok(rid, {"detached": True})


def _desktop_bridge_scope(session_id: str, profile_id: str, browser_profile_id: str,
                          controller_id: str, capabilities: frozenset) -> ControllerScope:
    from gateway.browser_control_broker import ControllerScope, local_desktop_principal
    return ControllerScope(
        principal_id=local_desktop_principal(profile_id, session_id),
        profile_id=profile_id,
        session_id=session_id,
        controller_id=controller_id,
        browser_profile_id=browser_profile_id,
        transport_family=LOCAL_DESKTOP_TRANSPORT_FAMILY,
        capabilities=capabilities,
    )


def _session_profile(session: dict) -> str:
    from hermes_constants import profile_name_for_home
    from tui_gateway.server import _current_profile_name
    return str(session.get('profile') or session.get('profile_id')
               or (profile_name_for_home(session['profile_home']) if session.get('profile_home')
                   else _current_profile_name()) or '').strip()


def _session_by_id(session_id: str):
    with _sessions_lock:
        return _sessions.get(session_id)


def _desktop_bridge_params(rid: str, params: dict):
    session_id = str(params.get('session_id') or '').strip()
    browser_profile_id = str(params.get('browser_profile_id') or '').strip()
    controller_id = str(params.get('controller_id') or '').strip()
    if not session_id:
        return None, _err(rid, _ERR_FORBIDDEN, 'session_id is required')
    session = _session_by_id(session_id)
    if not isinstance(session, dict):
        return None, _err(rid, _ERR_FORBIDDEN, 'session is not available on this Desktop gateway')
    profile_id = _session_profile(session)
    if not profile_id:
        return None, _err(rid, _ERR_FORBIDDEN, 'session profile is unavailable')
    return (session_id, profile_id, browser_profile_id, controller_id, session), None


@_controller_method('browser.controller.bridge_prepare', lookup_scope=False, precheck=_register_precheck, desktop_bridge=True)
def _(rid, params: dict, _transport, _identity, _session_id, broker, _scope, session) -> dict:
    import secrets
    from gateway import browser_control_broker
    prepared, denied = _desktop_bridge_params(rid, params)
    if denied is not None:
        return denied
    session_id, profile_id, browser_profile_id, _controller_id, _session = prepared
    if not browser_profile_id:
        return _err(rid, _ERR_FORBIDDEN, 'browser_profile_id is required')
    if not browser_control_broker.browser_control_protocol_supported(params.get('protocol_version')):
        return _err(rid, _ERR_FORBIDDEN, 'unsupported browser-control protocol version')
    capabilities = browser_control_broker.filter_browser_control_capabilities(params.get('capabilities'))
    if not capabilities:
        return _err(rid, _ERR_FORBIDDEN, 'no permitted controller capabilities requested')
    controller_id = f'chrome-{secrets.token_urlsafe(12)}'
    scope = _desktop_bridge_scope(session_id, profile_id, browser_profile_id, controller_id, capabilities)
    grant = broker.mint_bridge_grant(scope)
    return _ok(rid, {
        'launch_context': {
            'grant': grant.value,
            'session_id': session_id,
            'controller_id': controller_id,
            'browser_profile_id': browser_profile_id,
            'capabilities': sorted(capabilities),
            'protocol_version': BROWSER_CONTROL_PROTOCOL_VERSION,
            'profile_id': profile_id,
        },
        'expires_in_seconds': broker.launch_grant_ttl_seconds,
    })


@_controller_method('browser.controller.bridge_status', lookup_scope=False, desktop_bridge=True)
def _(rid, params: dict, _transport, _identity, _session_id, broker, _scope, _session) -> dict:
    from gateway.browser_control_broker import local_desktop_principal
    prepared, denied = _desktop_bridge_params(rid, params)
    if denied is not None:
        return denied
    session_id, profile_id, browser_profile_id, controller_id, _session = prepared
    scope = broker.scope_for_session(
        session_id=session_id,
        principal_id=local_desktop_principal(profile_id, session_id),
        transport_family=LOCAL_DESKTOP_TRANSPORT_FAMILY,
    )
    result = {'status': 'connected' if scope is not None else 'inactive'}
    if scope is not None:
        result.update({
            'session_id': scope.session_id,
            'controller_id': scope.controller_id,
            'browser_profile_id': scope.browser_profile_id,
            'capabilities': sorted(scope.capabilities),
        })
    return _ok(rid, result)


@_controller_method('browser.controller.bridge_revoke', lookup_scope=False, desktop_bridge=True)
def _(rid, params: dict, _transport, _identity, _session_id, broker, _scope, _session) -> dict:
    from gateway.browser_control_broker import local_desktop_principal
    prepared, denied = _desktop_bridge_params(rid, params)
    if denied is not None:
        return denied
    session_id, profile_id, browser_profile_id, controller_id, _session = prepared
    identity = local_desktop_principal(profile_id, session_id)
    scope = broker.scope_for_session(session_id=session_id, principal_id=identity,
                                    transport_family=LOCAL_DESKTOP_TRANSPORT_FAMILY)
    if scope is not None and controller_id and scope.controller_id != controller_id:
        return _err(rid, _ERR_FORBIDDEN, 'controller does not match this Desktop session')
    if scope is not None:
        broker.detach(scope, notify_controller=False)
    target = scope or _desktop_bridge_scope(session_id, profile_id, browser_profile_id or 'unknown', controller_id or 'unknown', frozenset())
    revoked = broker.revoke_bridge_grants(target)
    return _ok(rid, {'revoked': revoked, 'status': 'inactive'})


def register(server) -> None:
    """Publish helpers/constants onto ``server`` and install handlers (rebound to its globals)."""
    bind_module(globals(), server, skip=("_",))
