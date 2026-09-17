"""Registry-level browser extension router.

Agent-side half of browser-extension-control: decides, per ``browser_*`` handler
invocation, whether an attached extension controller (via
:mod:`gateway.browser_control_broker`) or the legacy backend executes it.

Routing contract (see ``tests/tools/test_browser_extension_router.py``):
- Feature off ⇒ legacy, broker never touched, ``fallback()`` called exactly once.
- No server-bound identity ⇒ legacy.
- Bound identity ⇒ authoritative extension lane; missing/ambiguous scope,
  disconnect, or capability mismatch fail closed (never jump to another browser).
- Selected controller ⇒ authoritative; its errors propagate, legacy never retried.
- ``args`` is never mutated.
- In Stardust personal-assistant contexts, ``browser_click`` is semantically
  preflighted from a fresh accessibility snapshot immediately before the effect.

:func:`routed_browser_handler` resolves the flag and broker lazily on every call
so importing this module never pulls in the gateway and config changes apply
without restart.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _bound_identity() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(session_id, principal_id, transport_family) from the session context."""
    from gateway.session_context import get_session_env

    return tuple(  # type: ignore[return-value]
        get_session_env(key, "") or None
        for key in ("HERMES_SESSION_ID", "HERMES_BROWSER_CONTROL_PRINCIPAL",
                    "HERMES_BROWSER_CONTROL_TRANSPORT_FAMILY")
    )


def _controller_unavailable(message: str) -> Exception:
    from gateway.browser_control_broker import ControllerUnavailable

    return ControllerUnavailable(message)


def extension_controller_available(action: str) -> bool:
    """Whether this request owns one exact controller capable of ``action``.
    Runs during tool-schema assembly inside the request's session context;
    consults the process-local broker directly and fails closed on any gap."""
    try:
        from gateway.browser_control_broker import browser_control_enabled, get_browser_control_broker

        if not browser_control_enabled():
            return False
        session_id, principal_id, transport_family = identity = _bound_identity()
        if not all(identity):
            return False
        broker = get_browser_control_broker()
        scope = broker.scope_for_session(session_id=session_id, principal_id=principal_id, transport_family=transport_family)
        return scope is not None and broker.select(scope, action) is not None
    except Exception:
        logger.debug("browser extension availability check failed for %s", action, exc_info=True)
        return False


def _browser_permission_failure(message: str) -> str:
    return json.dumps({"success": False, "status": "blocked", "error": message}, ensure_ascii=False)


def _legacy_effect_or_guard(
    action: str, args: Dict[str, Any], task_id: Optional[str], fallback: Callable[[], Any]
) -> Any:
    """Run the legacy backend, except semantic-preflight a personal-assistant click first."""
    if action != "browser_click":
        return fallback()
    try:
        from tools.browser_action_approval import guard_browser_click

        blocked = guard_browser_click(args.get("ref"), task_id)
    except Exception:
        logger.error("Stardust browser click permission guard failed", exc_info=True)
        return _browser_permission_failure(
            "Stardust could not verify this browser click safely. Refresh the page snapshot and retry."
        )
    return blocked if blocked is not None else fallback()


def _controller_click_guard(
    action: str,
    args: Dict[str, Any],
    *,
    broker: Any,
    scope: Any,
    tool_call_id: Optional[str],
) -> Optional[str]:
    """Semantic click approval using the authoritative extension controller's own fresh snapshot."""
    if action != "browser_click":
        return None

    try:
        from tools.browser_action_approval import (
            BrowserClickRisk, guard_browser_click_with_probe, inspect_controller_snapshot,
        )
    except Exception:
        logger.error("Stardust browser click approval module unavailable", exc_info=True)
        return _browser_permission_failure(
            "Stardust could not verify this browser click safely. Refresh the page snapshot and retry."
        )

    ref = args.get("ref")
    probe_index = 0

    def probe() -> BrowserClickRisk:
        nonlocal probe_index
        probe_index += 1
        try:
            # The same bound controller must be able to describe the exact page it will click.
            # If it cannot expose a fresh snapshot, consent cannot be bound to a concrete control.
            if broker.select(scope, "browser_snapshot") is None:
                return BrowserClickRisk(False, resolved=False)
            probe_call_id = f"{tool_call_id or 'browser-click'}:stardust-preflight:{probe_index}"
            snapshot = broker.dispatch(
                scope,
                action="browser_snapshot",
                arguments={"full": False},
                tool_call_id=probe_call_id,
            )
            return inspect_controller_snapshot(snapshot, ref)
        except Exception:
            logger.warning("Extension browser click preflight snapshot failed", exc_info=True)
            return BrowserClickRisk(False, resolved=False)

    return guard_browser_click_with_probe(ref, probe)


def route_browser_tool(
    action: str, args: Dict[str, Any], *, fallback: Callable[[], Any], broker: Any, enabled: bool,
    session_id: Optional[str] = None, task_id: Optional[str] = None, principal_id: Optional[str] = None,
    transport_family: Optional[str] = None, tool_call_id: Optional[str] = "",
) -> Any:
    """Route one browser action through the extension-control broker.

    ``broker`` exposes ``scope_for_session(**identity)``, ``select(scope, cap)``
    and ``dispatch(scope, *, action, arguments, tool_call_id)``. ``fallback`` is
    called exactly once when the feature is off or no server-bound identity
    exists; once a controller is selected its result/exception is final.
    """
    if not enabled or not str(principal_id or "").strip() or not str(transport_family or "").strip():
        return _legacy_effect_or_guard(action, args, task_id, fallback)

    identity = dict(session_id=session_id, task_id=task_id, principal_id=principal_id, transport_family=transport_family)
    scope = broker.scope_for_session(**identity)
    if scope is None:
        # A stamped identity only becomes authoritative once a controller has
        # registered for the lane; unregistered lanes keep the legacy backend,
        # registered-but-offline lanes fail closed.
        lane_bound = getattr(broker, "lane_registered", None)
        if callable(lane_bound) and not lane_bound(**identity):
            return _legacy_effect_or_guard(action, args, task_id, fallback)
        raise _controller_unavailable(f"bound browser controller unavailable for {action}")

    if broker.select(scope, action) is None:
        raise _controller_unavailable(f"bound browser controller cannot execute {action}")

    # Controller is authoritative: never retry the legacy backend. A consequential click is
    # preflighted from this exact controller's fresh accessibility snapshot before dispatch.
    blocked = _controller_click_guard(
        action, args, broker=broker, scope=scope, tool_call_id=tool_call_id
    )
    if blocked is not None:
        return blocked

    # Registry handlers must return a string; keep string results byte-identical and
    # serialize decoded JSON values at this boundary.
    result = broker.dispatch(scope, action=action, arguments=args, tool_call_id=tool_call_id)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def current_tool_call_id() -> str:
    """Active tool_call_id bound by the agent executor, or ``""`` when none."""
    try:
        from tools.approval_context import _approval_tool_call_id

        return _approval_tool_call_id.get() or ""
    except Exception:
        return ""


def routed_browser_handler(
    action: str, args: Dict[str, Any], *, fallback: Callable[[], Any], task_id: Optional[str] = None,
    session_id: Optional[str] = None, principal_id: Optional[str] = None,
    transport_family: Optional[str] = None, tool_call_id: Optional[str] = None,
) -> Any:
    """Lazy registry-handler route wrapper for ``browser_*`` tools.
    Feature off (or gateway unimportable) ⇒ the legacy handler runs unchanged,
    except Stardust click approval still executes at this final effect boundary."""
    try:
        from gateway.browser_control_broker import browser_control_enabled, get_browser_control_broker
    except Exception as exc:  # pragma: no cover - defensive, gateway always present
        logger.debug("browser extension router unavailable (%s); using legacy backend", exc)
        return _legacy_effect_or_guard(action, args, task_id, fallback)
    if not browser_control_enabled():
        return _legacy_effect_or_guard(action, args, task_id, fallback)

    try:
        env_session, env_principal, env_transport = _bound_identity()
    except Exception:
        env_session = env_principal = env_transport = None

    return route_browser_tool(
        action, args, fallback=fallback, broker=get_browser_control_broker(), enabled=True,
        session_id=session_id or env_session, task_id=task_id, principal_id=principal_id or env_principal,
        transport_family=transport_family or env_transport,
        tool_call_id=current_tool_call_id() if tool_call_id is None else tool_call_id,
    )
