"""Hermes lifecycle dispatch for first-party observers and plugins."""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)


def _observe(hook_name: str, **kwargs: Any) -> None:
    try:
        from hermes_cli.observability import observe_lifecycle

        observe_lifecycle(hook_name, **kwargs)
    except Exception:
        logger.warning("Built-in observability hook failed", exc_info=True)


def _plugin_hooks(hook_name: str, **kwargs: Any) -> List[Any]:
    from hermes_cli import plugins

    return plugins.invoke_hook(hook_name, **kwargs)


def _effective_pre_tool_args(plugin_results: List[Any], original_args: Any) -> dict[str, Any]:
    """Replay plugin ``modify`` directives exactly enough for first-party policy classification.

    Plugin dispatch remains the authority that actually applies these rewrites. This projection exists
    only so Stardust's permission policy sees the same effective arguments that will be dispatched: a
    plugin must not be able to turn a harmless read into a consequential write after the policy looked.
    """
    effective = dict(original_args) if isinstance(original_args, dict) else {}
    for result in plugin_results:
        if not isinstance(result, dict) or result.get("action") != "modify":
            continue
        partial = result.get("args")
        if isinstance(partial, dict) and partial:
            effective.update(partial)
    return effective


def _assistant_permission_directive(plugin_results: List[Any], **kwargs: Any) -> Any:
    """First-party ``pre_tool_call`` directive, evaluated after plugin argument rewrites.

    Existing plugin block/approval decisions keep their registration-order semantics. The Stardust
    policy is appended last, so ordinary plugin transforms happen first while high-risk calls still
    cross the same human approval gate used elsewhere in the runtime.
    """
    try:
        from hermes_cli.assistant_permissions import pre_tool_call_directive

        effective_args = _effective_pre_tool_args(plugin_results, kwargs.get("args"))
        return pre_tool_call_directive(str(kwargs.get("tool_name") or ""), effective_args)
    except Exception:
        # This gate only owns Desktop/durable-personal-assistant contexts. Fail closed there: a policy
        # crash must never become a silent authorization bypass for an external side effect.
        try:
            from hermes_cli.assistant_permissions import personal_assistant_permissions_active

            if personal_assistant_permissions_active():
                logger.error("Stardust assistant permission policy failed; blocking tool call", exc_info=True)
                return {
                    "action": "block",
                    "message": "Stardust permission policy failed; this action was not executed.",
                }
        except Exception:
            logger.error("Unable to determine Stardust assistant permission scope", exc_info=True)
        return None


def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    """Notify first-party observers, invoke plugins, then apply first-party control policy."""
    _observe(hook_name, **kwargs)
    results = _plugin_hooks(hook_name, **kwargs)
    if hook_name == "pre_tool_call":
        directive = _assistant_permission_directive(results, **kwargs)
        if directive is not None:
            results = [*results, directive]
    return results


def has_hook(hook_name: str) -> bool:
    """Return whether a first-party observer/policy or plugin consumes a hook."""
    try:
        from hermes_cli.observability import handles_hook

        if handles_hook(hook_name):
            return True
    except Exception:
        logger.warning("Unable to inspect built-in observability hooks", exc_info=True)

    if hook_name == "pre_tool_call":
        try:
            from hermes_cli.assistant_permissions import personal_assistant_permissions_active

            if personal_assistant_permissions_active():
                return True
        except Exception:
            # If the policy scope cannot be inspected, do not advertise a hook here. The execution path
            # invokes pre_tool_call directly and fail-closes inside _assistant_permission_directive.
            logger.warning("Unable to inspect Stardust assistant permission policy", exc_info=True)

    from hermes_cli import plugins

    return plugins.has_hook(hook_name)


def finalize_session(**kwargs: Any) -> List[Any]:
    """Notify observers and hard-close one core-owned Relay conversation."""
    _observe("on_session_finalize", **kwargs)

    session_id = str(kwargs.get("session_id") or "")
    if session_id:
        try:
            from agent import relay_runtime

            relay_runtime.SESSION_COORDINATOR.finalize_conversation(
                profile_key=relay_runtime.current_profile_key(),
                session_id=session_id,
            )
        except Exception:
            logger.warning("Core Relay session finalization failed", exc_info=True)

    return _plugin_hooks("on_session_finalize", **kwargs)
