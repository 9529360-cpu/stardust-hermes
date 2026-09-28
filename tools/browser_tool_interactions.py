"""Advanced browser transaction interactions over the existing browser session.

This is deliberately one compact model tool rather than one schema per verb. The
actual browser/session ownership stays in :mod:`tools.browser_tool`; this module
only validates the higher-level action and maps it onto agent-browser commands.

Upload/download are intentionally not here: file transfer has a separate
artifact/provenance boundary and must never accept arbitrary host paths through
this generic interaction surface.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from tools.browser_tool_origin import origin_module as _origin
from tools import browser_tool_install as _install
from tools import browser_tool_lightpanda_fallback as _lp


BROWSER_INTERACT_ACTIONS = (
    "hover",
    "select",
    "check",
    "uncheck",
    "drag",
    "scroll_into_view",
    "wait_element",
    "wait_text",
    "wait_url",
    "wait_load",
)

BROWSER_INTERACT_SCHEMA: Dict[str, Any] = {
    "name": "browser_interact",
    "description": (
        "Perform advanced page interactions that are common in real forms and transactional websites "
        "without falling back to raw CDP: hover menus, select dropdown options, check/uncheck controls, "
        "drag-and-drop, scroll an element into view, or wait for a meaningful page condition. "
        "Use refs from browser_navigate/browser_snapshot when an element is involved. Prefer condition-based "
        "waits (element/text/url/load) after page-changing actions instead of repeatedly polling snapshots. "
        "This tool does not upload/download files; those cross a separate artifact security boundary. "
        "Not available on browser backends that cannot preserve these interactions in the active session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(BROWSER_INTERACT_ACTIONS),
                "description": "Advanced interaction or wait operation to perform.",
            },
            "ref": {
                "type": "string",
                "description": (
                    "Source element ref (for example @e5). Required for hover/select/check/uncheck/"
                    "drag/scroll_into_view/wait_element. wait_element also accepts a CSS selector."
                ),
            },
            "target_ref": {
                "type": "string",
                "description": "Destination element ref for drag.",
            },
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more option values or visible labels for select.",
            },
            "text": {
                "type": "string",
                "description": "Text substring to wait for when action=wait_text.",
            },
            "url_pattern": {
                "type": "string",
                "description": "URL glob/pattern to wait for when action=wait_url, e.g. **/dashboard.",
            },
            "load_state": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
                "description": (
                    "Lifecycle state for wait_load. Prefer load/domcontentloaded; use networkidle only "
                    "for pages known to become quiet."
                ),
            },
            "state": {
                "type": "string",
                "enum": ["visible", "hidden"],
                "description": "For wait_element: wait until the selector/ref is visible (default) or hidden.",
                "default": "visible",
            },
        },
        "required": ["action"],
    },
}


def _error(message: str) -> str:
    bt = _origin()
    return bt._dumps(bt._err(message))


def _backend_block_reason() -> Optional[str]:
    """Why this interaction surface cannot preserve state on the active backend."""
    bt = _origin()
    if bt._is_camofox_mode():
        return (
            "browser_interact is not supported by the active Camofox REST backend. "
            "Use the ordinary browser tools it exposes for this session."
        )
    if bt._is_browser_use_cli_mode():
        return (
            "browser_interact is replaced by browser_exec while Browser Use mode is active; "
            "perform the equivalent interaction inside browser_exec."
        )
    lightpanda_active, reason = _lp.lightpanda_engine_status()
    if lightpanda_active:
        return (
            "browser_interact requires a state-preserving Chrome/CDP session. "
            f"The active built-in Lightpanda route cannot preserve these advanced actions ({reason})."
        )
    return None


def check_browser_interact_requirements() -> bool:
    """Static/session availability gate for the advanced interaction surface."""
    if _backend_block_reason() is not None:
        return False
    return _install.check_browser_requirements()


def _require_text(value: Any, field: str, action: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{action} requires non-empty {field}.")
    return text


def _element_target(value: Any, field: str, action: str, *, allow_selector: bool = False) -> str:
    raw = _require_text(value, field, action)
    if allow_selector and not raw.startswith("@"):
        return raw
    return _origin()._at_ref(raw)


def _command_for(
    action: str,
    *,
    ref: Optional[str],
    target_ref: Optional[str],
    values: Optional[list[str]],
    text: Optional[str],
    url_pattern: Optional[str],
    load_state: Optional[str],
    state: Optional[str],
) -> tuple[str, list[str], Dict[str, Any], bool]:
    """Validate one action and return command, argv, success payload, is_wait."""
    if action == "hover":
        element = _element_target(ref, "ref", action)
        return "hover", [element], {"hovered": element}, False

    if action == "select":
        element = _element_target(ref, "ref", action)
        selected = [str(v).strip() for v in (values or []) if str(v).strip()]
        if not selected:
            raise ValueError("select requires at least one non-empty value in values.")
        return "select", [element, *selected], {"element": element, "selected": selected}, False

    if action in {"check", "uncheck"}:
        element = _element_target(ref, "ref", action)
        return action, [element], {"element": element, "checked": action == "check"}, False

    if action == "drag":
        source = _element_target(ref, "ref", action)
        target = _element_target(target_ref, "target_ref", action)
        return "drag", [source, target], {"dragged": source, "target": target}, False

    if action == "scroll_into_view":
        element = _element_target(ref, "ref", action)
        return "scrollintoview", [element], {"scrolled_into_view": element}, False

    if action == "wait_element":
        selector = _element_target(ref, "ref", action, allow_selector=True)
        wait_state = state or "visible"
        if wait_state not in {"visible", "hidden"}:
            raise ValueError("wait_element state must be 'visible' or 'hidden'.")
        args = [selector] if wait_state == "visible" else [selector, "--state", "hidden"]
        return "wait", args, {"waited_for": "element", "selector": selector, "state": wait_state}, True

    if action == "wait_text":
        expected = _require_text(text, "text", action)
        return "wait", ["--text", expected], {"waited_for": "text", "text": expected}, True

    if action == "wait_url":
        pattern = _require_text(url_pattern, "url_pattern", action)
        return "wait", ["--url", pattern], {"waited_for": "url", "url_pattern": pattern}, True

    if action == "wait_load":
        load = load_state or "load"
        if load not in {"load", "domcontentloaded", "networkidle"}:
            raise ValueError(
                "wait_load load_state must be 'load', 'domcontentloaded', or 'networkidle'."
            )
        return "wait", ["--load", load], {"waited_for": "load", "load_state": load}, True

    raise ValueError(
        f"Unknown browser_interact action {action!r}. Use one of: {', '.join(BROWSER_INTERACT_ACTIONS)}."
    )


def browser_interact(
    action: str,
    ref: Optional[str] = None,
    target_ref: Optional[str] = None,
    values: Optional[list[str]] = None,
    text: Optional[str] = None,
    url_pattern: Optional[str] = None,
    load_state: Optional[str] = None,
    state: Optional[str] = "visible",
    task_id: Optional[str] = None,
) -> str:
    """Execute one validated advanced interaction in the task's current browser session."""
    bt = _origin()
    blocked_backend = _backend_block_reason()
    if blocked_backend:
        return _error(blocked_backend)

    action = str(action or "").strip().lower()
    try:
        command, args, success_payload, is_wait = _command_for(
            action,
            ref=ref,
            target_ref=target_ref,
            values=values,
            text=text,
            url_pattern=url_pattern,
            load_state=load_state,
            state=state,
        )
    except ValueError as exc:
        return _error(str(exc))

    effective_task_id = bt._last_session_key(task_id or "default")
    if is_wait:
        blocked = bt._blocked_private_page_content(effective_task_id)
    else:
        blocked = bt._blocked_private_page_action(effective_task_id, action)
    if blocked is not None:
        return blocked

    result = bt._session._run_browser_command(effective_task_id, command, args)
    if not result.get("success"):
        return bt._failed_response(result, f"browser_interact {action} failed")

    # A wait may finish because the page navigated while we were blocked. Re-check
    # before returning any page-derived condition details so private/LAN content
    # cannot be observed through the wait surface.
    if is_wait:
        blocked = bt._blocked_private_page_content(effective_task_id)
        if blocked is not None:
            return blocked

    return bt._json_with_fallback({"success": True, "action": action, **success_payload}, result)
