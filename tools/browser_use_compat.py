"""Compatibility contract for the Browser Use ``browser_exec`` surface.

This is deliberately a small, data-only boundary: it describes what the CLI can
prove locally and maps the legacy browser capabilities to safe exec-side helpers.
It does not start Python, a browser, or a host process.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


# Evidence sources are code paths in this repository, not claims about a remote
# Browser Use release. Keep this table reviewable when the CLI changes.
CAPABILITY_MATRIX = {
    "status": {"legacy": "browser_status", "exec": "page_info", "support": "adapter"},
    "console_errors": {"legacy": "browser_console", "exec": "cdp('Runtime.enable')", "support": "adapter"},
    "vision_annotation": {"legacy": "browser_vision(annotate=True)", "exec": "capture_screenshot + cdp", "support": "partial"},
    "structured_actions": {"legacy": "browser_interact", "exec": "js/cdp/click_at_xy", "support": "mapping"},
    "execution_errors": {"legacy": "browser_* error JSON", "exec": "success/error/error_type", "support": "native"},
}

_ACTIONS = {
    "navigate": "new_tab/goto_url",
    "snapshot": "page_info/js",
    "click": "js/click_at_xy",
    "type": "fill_input/js",
    "scroll": "js/cdp",
    "press": "cdp",
    "evaluate": "js",
    "screenshot": "capture_screenshot",
}


def capability_matrix() -> Dict[str, Dict[str, str]]:
    """Return a copy so callers cannot mutate the contract."""
    return {name: dict(row) for name, row in CAPABILITY_MATRIX.items()}


def structured_action_mapping(action: str, args: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Map a legacy action name to an explicit exec helper without executing it.

    Unknown actions fail loudly; silently dropping an action would reduce browser
    capability and make migration failures look like successful no-ops.
    """
    if action not in _ACTIONS:
        raise ValueError(f"Unsupported browser action: {action}")
    return {"action": action, "exec_helper": _ACTIONS[action], "args": dict(args or {})}


def compatibility_status() -> Dict[str, Any]:
    """Machine-readable evidence-backed status for diagnostics and tests."""
    return {"contract": "stardust.browser-exec-compat.v1", "host_python": False,
            "capabilities": capability_matrix(), "actions": sorted(_ACTIONS)}
