"""ACP permission bridging for Hermes dangerous-command approvals."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from itertools import count
from typing import Any, Callable

from acp.schema import AllowedOutcome, PermissionOption

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalTrustPosture:
    """Immutable trust decision for dangerous-command approvals on one ACP connection.

    ACP proves only that a client returned a permission outcome. It does not prove that
    a human saw or selected that outcome. Client identity/capabilities are captured once
    at initialize time for observability; trust comes only from the operator's explicit
    security.approval.acp_trusted_clients allowlist.
    """

    client_name: str = "unknown"
    client_version: str = ""
    capability_names: tuple[str, ...] = ()
    trusted_interactive: bool = False
    reason: str = "untrusted"


def _capability_names(client_capabilities: object) -> tuple[str, ...]:
    """Stable top-level capability names for the immutable connection snapshot."""
    if client_capabilities is None:
        return ()
    try:
        if hasattr(client_capabilities, "model_dump"):
            raw = client_capabilities.model_dump(by_alias=True, exclude_none=True)
        elif isinstance(client_capabilities, dict):
            raw = client_capabilities
        else:
            raw = vars(client_capabilities)
    except Exception:
        return ()
    return tuple(sorted(str(key) for key in raw)) if isinstance(raw, dict) else ()


def capture_approval_trust_posture(
    client_info: object = None,
    client_capabilities: object = None,
    *,
    config: dict[str, Any] | None = None,
) -> ApprovalTrustPosture:
    """Resolve the per-connection ACP dangerous-command trust posture once, fail-closed.

    Client identity is not inferred to be interactive. The exact ACP client_info.name
    must be explicitly allowlisted. Wildcards are intentionally unsupported.
    """
    client_name = str(getattr(client_info, "name", "") or "").strip() or "unknown"
    client_version = str(getattr(client_info, "version", "") or "").strip()
    capability_names = _capability_names(client_capabilities)

    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            config = load_config_readonly() or {}
        except Exception:
            logger.warning(
                "Could not read ACP approval trust config; dangerous-command approvals will deny",
                exc_info=True,
            )
            return ApprovalTrustPosture(
                client_name=client_name,
                client_version=client_version,
                capability_names=capability_names,
                reason="config_unavailable",
            )

    security = config.get("security") if isinstance(config, dict) else None
    approval = security.get("approval") if isinstance(security, dict) else None
    raw_trusted = approval.get("acp_trusted_clients", []) if isinstance(approval, dict) else []

    if isinstance(raw_trusted, str):
        configured = [raw_trusted]
    elif isinstance(raw_trusted, (list, tuple, set)):
        configured = list(raw_trusted)
    else:
        logger.warning(
            "Invalid security.approval.acp_trusted_clients; dangerous-command approvals will deny"
        )
        return ApprovalTrustPosture(
            client_name=client_name,
            client_version=client_version,
            capability_names=capability_names,
            reason="invalid_trust_config",
        )

    trusted_names = {
        item.strip().casefold()
        for item in configured
        if isinstance(item, str) and item.strip() and item.strip() != "*"
    }
    trusted = client_name != "unknown" and client_name.casefold() in trusted_names
    return ApprovalTrustPosture(
        client_name=client_name,
        client_version=client_version,
        capability_names=capability_names,
        trusted_interactive=trusted,
        reason="configured_trusted_client" if trusted else "client_not_trusted",
    )

# ACP permission option id -> Hermes approval result. Ids are stable across the
# ``allow_permanent=True`` and ``False`` paths even though the option list differs.
_OPTION_ID_TO_HERMES = {
    "allow_once": "once", "allow_session": "session", "allow_always": "always", "deny": "deny", "deny_always": "deny"
}

_PERMISSION_REQUEST_IDS = count(1)


def _permission_option_supports_kind(kind: str) -> bool:
    """Return whether the installed ACP SDK accepts a permission option kind."""
    try:
        PermissionOption(option_id="__probe__", kind=kind, name="probe")
        return True
    except Exception:
        return False


def _build_permission_options(
    *, allow_permanent: bool, allow_session: bool = True, smart_denied: bool = False,
) -> list[PermissionOption]:
    """Return ACP options that match Hermes approval semantics."""
    # A gate that re-asks every time (allow_session=False, e.g. protected
    # agent-instruction writes) collapses to the same two options as a Smart
    # DENY override — offering a scope Hermes discards would re-prompt every write.
    # See #81887.
    once_only = smart_denied or not allow_session
    options = [PermissionOption(option_id="allow_once", kind="allow_once", name="Allow once")]
    if not once_only:
        # ACP has no session-scoped kind: closest persistent hint, Hermes semantics in the id.
        options.append(PermissionOption(option_id="allow_session", kind="allow_always", name="Allow for session"))
        if allow_permanent:
            options.append(PermissionOption(option_id="allow_always", kind="allow_always", name="Allow always"))
    options.append(PermissionOption(option_id="deny", kind="reject_once", name="Deny"))
    if not once_only and _permission_option_supports_kind("reject_always"):
        options.append(PermissionOption(option_id="deny_always", kind="reject_always", name="Deny always"))
    return options


def _build_permission_tool_call(command: str, description: str):
    """Return the ``ToolCallUpdate`` (not ``ToolCallStart``) payload attached to a
    permission request; unique ``perm-check-N`` ids keep concurrent requests apart."""
    import acp as _acp

    content_text = f"{description}\n$ {command}" if description else f"$ {command}"
    return _acp.update_tool_call(
        f"perm-check-{next(_PERMISSION_REQUEST_IDS)}", title=f"{description}: {command}" if description else command,
        kind="execute", status="pending", content=[_acp.tool_content(_acp.text_block(content_text))],
        raw_input={"command": command, "description": description},
    )


def _map_outcome_to_hermes(outcome: object, *, allowed_option_ids: set[str]) -> str:
    """Map an ACP permission outcome into Hermes approval strings."""
    if not isinstance(outcome, AllowedOutcome):
        return "deny"
    if outcome.option_id not in allowed_option_ids:
        logger.warning("Permission request returned unknown option_id: %s", outcome.option_id)
        return "deny"
    return _OPTION_ID_TO_HERMES.get(outcome.option_id, "deny")


def await_permission(
    request_permission_fn: Callable, loop: asyncio.AbstractEventLoop, session_id: str, *,
    tool_call, options: list[PermissionOption], timeout: float, what: str,
) -> tuple[object | None, bool]:
    """Schedule ``request_permission`` on ``loop`` from a worker thread and block for the answer.
    Returns ``(response, timed_out)``; ``(None, False)`` when scheduling or the request failed."""
    from agent.async_utils import safe_schedule_threadsafe

    coro = request_permission_fn(session_id=session_id, tool_call=tool_call, options=options)
    future = safe_schedule_threadsafe(coro, loop, logger=logger, log_message=f"{what}: failed to schedule on loop")
    if future is None:
        return None, False
    try:
        return future.result(timeout=timeout), False
    except FutureTimeout:
        future.cancel()
        logger.warning("%s timed out after %ss", what, timeout)
        return None, True
    except Exception as exc:
        future.cancel()
        logger.warning("%s failed: %s", what, exc)
        return None, False


def make_approval_callback(
    request_permission_fn: Callable,
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = 60.0,
    *,
    trust_posture: ApprovalTrustPosture | None = None,
) -> Callable[..., str]:
    """Bridge dangerous-command approval only for an explicitly trusted ACP host.

    A wire-level ACP allow is accepted only when the operator trusted this connection's
    client identity at initialize time. Unknown or untrusted clients deny without even
    sending a permission request because their response cannot prove a human was present.
    """
    posture = trust_posture or ApprovalTrustPosture(reason="missing_trust_posture")

    def _callback(command: str, description: str, *, allow_permanent: bool = True,
                  allow_session: bool = True, smart_denied: bool = False, **_: object) -> str:
        if not posture.trusted_interactive:
            logger.warning(
                "Denied ACP dangerous-command approval: client=%s version=%s reason=%s",
                posture.client_name,
                posture.client_version or "unknown",
                posture.reason,
            )
            return "deny"

        options = _build_permission_options(allow_permanent=allow_permanent, allow_session=allow_session,
                                            smart_denied=smart_denied)
        response, timed_out = await_permission(
            request_permission_fn, loop, session_id, tool_call=_build_permission_tool_call(command, description),
            options=options, timeout=timeout, what="Permission request",
        )
        if timed_out:
            # Distinct from an explicit deny: tools.approval reports "timed out
            # without user response" instead of a user denial.
            return "timeout"
        if response is None:
            return "deny"
        return _map_outcome_to_hermes(response.outcome, allowed_option_ids={option.option_id for option in options})

    return _callback
