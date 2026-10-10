"""One desktop Browser surface with explicit, turn-scoped target ownership.

This is a thin adapter, not another browser engine. The in-app target delegates
to the same Electron WebView bridges as desktop_preview/drive_preview; the host
target requires the existing authenticated, session-bound browser-control
controller. Neither lane ever falls back to a different browser on failure.
"""

from __future__ import annotations

import json
from typing import Any

from tools.registry import registry, tool_error

ACTIONS = (
    "open", "elements", "read", "click", "hover", "type", "scroll",
    "press", "back", "forward", "reload", "status",
)
TARGETS = ("in_app", "host")
HOST_ACTIONS = {
    "open": "browser_navigate",
    "elements": "browser_snapshot",
    "read": "browser_snapshot",
    "click": "browser_click",
    "type": "browser_type",
    "scroll": "browser_scroll",
    "press": "browser_press",
    "back": "browser_back",
}


def _success(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("success") is not False and data.get("ok") is not False and not (
        "error" in data and data.get("success") is not True
    )


def _host_action(action: str, args: dict, *, task_id: str, session_id: str, tool_call_id: str) -> str:
    command = HOST_ACTIONS.get(action)
    if command is None:
        return tool_error(
            f"The connected host controller does not support {action}; "
            "do not silently switch to an in-app browser."
        )

    # A registered, exactly matched controller is mandatory. CDP/profile-copy
    # backends cannot masquerade as control of the user's real Chrome tabs.
    from tools.browser_extension_router import extension_controller_available, routed_browser_handler

    if not extension_controller_available(command):
        return tool_error(
            "No authorized host browser controller is attached to this session "
            f"with permission for {command}. Connect and approve a compatible "
            "browser extension before using target=host."
        )
    if action in ("click", "type", "press"):
        from tools.approval import request_tool_approval
        from tools.browser_preview_approval import classify_browser_preview_action

        risk = classify_browser_preview_action("browser", action, args)
        decision = request_tool_approval("browser", risk.reason, rule_key=risk.approval_key)
        if not decision.get("approved"):
            return tool_error(decision.get("message") or "Host browser action denied.")

    payload: dict[str, Any] = {}
    if action == "open":
        payload["url"] = args["url"]
    elif action in ("elements", "read"):
        payload["full"] = action == "read" or args.get("full") is True
    elif action == "click":
        payload["ref"] = args.get("ref", "")
    elif action == "type":
        payload = {"ref": args.get("ref", ""), "text": args.get("text", "")}
    elif action == "scroll":
        payload["direction"] = args.get("direction", "down")
    elif action == "press":
        payload["key"] = args.get("key", "")
    return routed_browser_handler(
        command, payload,
        fallback=lambda: tool_error("Host browser controller disconnected; no fallback was performed."),
        task_id=task_id, session_id=session_id, tool_call_id=tool_call_id,
    )


def _in_app_action(action: str, args: dict, *, drive_callback, read_callback) -> str:
    if action == "open":
        from tools.open_preview_tool import open_preview_tool
        return open_preview_tool(url=args["url"])

    if action == "read":
        from tools.read_preview_tool import read_preview_tool
        return read_preview_tool(callback=read_callback, start=0, count=args.get("max", 12000))

    from tools.drive_preview_tool import drive_preview_tool

    verb = "elements" if action == "elements" else action
    amount = args.get("amount")
    if verb == "scroll" and amount is None:
        amount = 500 if args.get("direction", "down") == "down" else -500
    return drive_preview_tool(
        action=verb,
        ref=args.get("ref"),
        selector=args.get("selector"),
        text=args.get("text"),
        key=args.get("key"),
        submit=args.get("submit"),
        amount=amount,
        limit=args.get("max"),
        full=args.get("full"),
        callback=drive_callback,
    )


def run_unified_browser(agent: Any, args: dict, *, drive_callback, read_callback,
                        task_id: str = "", tool_call_id: str = "") -> str:
    """Dispatch one Browser call, pinning exactly one surface for this agent turn.

    The agent is the existing session owner. No new process-global registry or
    profile/cookie cache is introduced. A new turn may select a different target;
    an in-flight turn may not silently hop between targets.
    """
    if str(getattr(agent, "platform", "") or "").lower() != "desktop":
        return tool_error("Unified Browser requires an active Stardust desktop session.")

    action = str(args.get("action") or "").strip().lower()
    target = str(args.get("target") or "").strip().lower()
    if action not in ACTIONS:
        return tool_error(f"action must be one of: {', '.join(ACTIONS)}.")
    if target not in TARGETS:
        return tool_error("target must be in_app or host; select the actual browser for each action.")

    turn_id = str(getattr(agent, "_current_turn_id", "") or "")
    if not turn_id:
        return tool_error("Browser action has no live turn identity; refusing to pick an unowned session.")

    state = getattr(agent, "_stardust_browser_turn", None)
    if not isinstance(state, dict) or state.get("turn_id") != turn_id:
        state = {"turn_id": turn_id, "target": None}
        agent._stardust_browser_turn = state

    selected = state.get("target")
    if selected is not None and selected != target:
        return tool_error(
            f"This turn already controls {selected}. Cannot switch to {target} mid-turn. "
            "Start a new turn and explicitly select the other browser."
        )

    if action == "status":
        if target == "host":
            from tools.browser_extension_router import extension_controller_available
            available = extension_controller_available("browser_snapshot")
        else:
            available = drive_callback is not None and read_callback is not None
        return json.dumps({
            "success": True, "active_target": selected, "requested_target": target,
            "available": available,
        }, ensure_ascii=False)

    if action == "open":
        raw_url = str(args.get("url") or "").strip()
        from urllib.parse import urlsplit
        from tools.open_preview_tool import _normalize_target
        from tools.browser_tool import _secret_url_error

        url = _normalize_target(raw_url)
        parsed = urlsplit(url)
        if (not url or parsed.scheme.lower() not in ("http", "https")
                or not parsed.hostname or parsed.username or parsed.password):
            return tool_error("open requires an http(s) URL without embedded credentials.")
        blocked = _secret_url_error(url)
        if blocked is not None:
            return json.dumps(blocked, ensure_ascii=False)
        args = {**args, "url": url}

    if action in ("click", "type", "hover") and not (args.get("ref") or args.get("selector")):
        return tool_error(f"{action} requires a ref from elements or a selector.")
    if target == "host" and action in ("click", "type") and not args.get("ref"):
        return tool_error("Host browser actions require a ref returned by that browser's elements action.")
    if target == "host" and action == "type" and args.get("submit"):
        return tool_error("Host browser type does not support submit; use press with key=Enter separately.")
    if action == "type" and args.get("text") is None:
        return tool_error("type requires text.")
    if action == "press" and not args.get("key"):
        return tool_error("press requires key.")
    if action == "scroll" and args.get("direction", "down") not in ("up", "down"):
        return tool_error("scroll direction must be up or down.")
    if action == "press" and target == "in_app" and not (args.get("ref") or args.get("selector")):
        return tool_error("In-app press requires an element ref or selector.")

    # Lock the selected surface BEFORE dispatch, including denied/disconnected
    # actions. A failed host action must never let a later call in the same turn
    # drift onto an unrelated in-app page as an implicit retry.
    state["target"] = target
    if target == "host":
        result = _host_action(action, args, task_id=task_id,
                              session_id=str(getattr(agent, "session_id", "") or ""),
                              tool_call_id=tool_call_id)
    else:
        result = _in_app_action(action, args, drive_callback=drive_callback, read_callback=read_callback)

    return result


UNIFIED_BROWSER_SCHEMA = {
    "name": "browser",
    "description": (
        "Stardust's ONE desktop browser control entry. Choose the actual target "
        "on EVERY call: target=in_app (default for browsing; controls the live "
        "right-hand Browser WebView) or target=host (ONLY after the user's "
        "explicit request to use existing Chrome/Edge, and ONLY after a browser "
        "extension controller has been approved and attached). Actions: open "
        "(navigates/reuses the current page, NOT a new tab), elements (interactive "
        "refs), read (visible text), click, hover, type, scroll, press, back, "
        "forward, reload, status. Ref IDs are scoped to the selected target. "
        "Do not send actions to the other target or copy its URL to a second "
        "window. Hiding the right rail does not cancel in-app browsing. Host "
        "mode never copies Chrome cookies or switches to another browser on "
        "disconnect. Never type passwords, OTPs or payment secrets via model "
        "text; ask the user to enter them in the exact active browser, or "
        "use a secure vault ONLY when verified to address that same session. "
        "Start with open and then elements. Host control currently supports "
        "only open, elements/read, click, type, scroll, back and press."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "target": {
                "type": "string", "enum": list(TARGETS),
                "description": "in_app for the actual right-rail page; host only for an approved existing Chrome/Edge tab.",
            },
            "url": {"type": "string", "description": "open: http(s) URL, existing tab navigates instead of multiplying tabs."},
            "ref": {"type": "string", "description": "Element ref returned by elements."},
            "selector": {"type": "string", "description": "CSS selector alternative (in_app only)."},
            "text": {"type": "string", "description": "type: text to input."},
            "submit": {"type": "boolean", "description": "type (in_app): press Enter after typing."},
            "key": {"type": "string", "description": "press: key name."},
            "direction": {"type": "string", "enum": ["up", "down"], "description": "scroll direction."},
            "amount": {"type": "integer", "description": "scroll pixels for in_app (signed)."},
            "max": {"type": "integer", "description": "Maximum elements or text length."},
            "full": {"type": "boolean", "description": "Return all interactive elements, not just changes."},
        },
        "required": ["action", "target"],
    },
}

# Registry fallback deliberately cannot run: the agent owns the authenticated
# GUI callbacks. The session-scoped inline executor is the only execution path.
registry.register(
    name="browser",
    toolset="desktop_ui",
    schema=UNIFIED_BROWSER_SCHEMA,
    handler=lambda args, **kw: tool_error(
        "Browser controller requires a live agent/session; use the desktop conversation."
    ),
    emoji="🌐",
)
