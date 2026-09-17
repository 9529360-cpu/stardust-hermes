"""Approval guard for advanced browser escape hatches in Stardust assistant sessions.

Normal browsing stays low-friction. This module only upgrades operations that bypass the
semantic browser tools: arbitrary page JavaScript through ``browser_console(expression=...)``
and raw CDP methods that are not on a deliberately small read-only allowlist.

Approval persistence receives only an opaque action fingerprint, never raw JavaScript or CDP
params. That keeps the approval ledger from becoming a second store for page contents, tokens,
or form data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class BrowserEscapeRisk:
    requires_approval: bool
    context_sha256: str = ""
    reason: str = ""
    rule_key: str = ""


# Keep this intentionally narrow. Raw CDP is an expert escape hatch; methods not proven to be
# observational are confirmed instead of guessed safe from a verb prefix.
_READ_ONLY_CDP_METHODS = frozenset({
    "Accessibility.getFullAXTree",
    "Browser.getVersion",
    "DOM.describeNode",
    "DOM.getDocument",
    "DOM.getOuterHTML",
    "DOM.querySelector",
    "DOM.querySelectorAll",
    "Page.captureScreenshot",
    "Page.getFrameTree",
    "Page.getLayoutMetrics",
    "Page.getNavigationHistory",
    "Performance.getMetrics",
    "Target.getTargetInfo",
    "Target.getTargets",
})


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def inspect_browser_escape(action: str, args: Optional[Mapping[str, Any]] = None) -> BrowserEscapeRisk:
    """Classify advanced browser operations without performing them."""
    values: Mapping[str, Any] = args if isinstance(args, Mapping) else {}

    if action == "browser_console":
        expression = values.get("expression")
        if expression is None or not str(expression).strip():
            return BrowserEscapeRisk(False)
        context_sha256 = _fingerprint({"action": action, "expression": str(expression)})
        return BrowserEscapeRisk(
            True,
            context_sha256=context_sha256,
            reason=(
                "Stardust wants to run arbitrary JavaScript in the current page through the browser console. "
                "This escape hatch can submit forms, click controls, navigate, change page storage, or issue network requests; confirm first."
            ),
            rule_key=f"stardust:browser-escape:console:{context_sha256[:20]}",
        )

    if action == "browser_cdp":
        method = str(values.get("method") or "").strip()
        if not method or method in _READ_ONLY_CDP_METHODS:
            return BrowserEscapeRisk(False)
        context_sha256 = _fingerprint({
            "action": action,
            "method": method,
            "params": values.get("params"),
            "target_id": values.get("target_id"),
            "frame_id": values.get("frame_id"),
        })
        return BrowserEscapeRisk(
            True,
            context_sha256=context_sha256,
            reason=(
                f"Stardust wants to send raw Chrome DevTools Protocol method '{method}'. "
                "Raw CDP can execute page JavaScript, navigate, modify browser/page state, or bypass higher-level browser safeguards; confirm first."
            ),
            rule_key=f"stardust:browser-escape:cdp:{context_sha256[:20]}",
        )

    return BrowserEscapeRisk(False)


def _blocked_payload(message: str, *, status: str = "waiting_confirmation") -> str:
    return json.dumps({"success": False, "status": status, "error": message}, ensure_ascii=False)


def guard_browser_escape(action: str, args: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """Return blocked-result JSON when approval is needed/denied, else ``None`` to execute."""
    try:
        from hermes_cli.assistant_permissions import (
            _durable_worker_confirmation_active,
            personal_assistant_permissions_active,
        )
    except Exception:
        return _blocked_payload(
            "Stardust could not evaluate advanced browser permissions. The action was not executed.",
            status="blocked",
        )

    if not personal_assistant_permissions_active():
        return None

    first = inspect_browser_escape(action, args)
    if not first.requires_approval:
        return None

    approval_args = {"context_sha256": first.context_sha256}
    if _durable_worker_confirmation_active():
        try:
            from tools.background_task_approval import authorize_or_block_current_worker

            result = authorize_or_block_current_worker(
                action,
                approval_args,
                reason=first.reason,
                rule_key=first.rule_key,
            )
        except Exception:
            return _blocked_payload(
                "Stardust could not persist the required advanced-browser approval. The action was not executed.",
                status="blocked",
            )
        if not result.allowed:
            return _blocked_payload(result.message)
    else:
        try:
            from tools.approval import request_tool_approval

            decision = request_tool_approval(action, first.reason, rule_key=first.rule_key)
        except Exception:
            return _blocked_payload(
                "Stardust could not request approval for this advanced browser action. The action was not executed.",
                status="blocked",
            )
        if not decision.get("approved"):
            message = decision.get("user_summary") or decision.get("message") or "The browser action was not approved."
            return _blocked_payload(str(message), status=str(decision.get("status") or "blocked"))

    # Approval callbacks may wait. Re-fingerprint the exact args at the final effect boundary so a
    # mutated expression/method/params object cannot consume consent issued for different content.
    second = inspect_browser_escape(action, args)
    if not second.requires_approval or second.context_sha256 != first.context_sha256:
        return _blocked_payload(
            "BLOCKED: the advanced browser action changed after approval. Request approval again for the current action.",
            status="blocked",
        )
    return None


__all__ = ["BrowserEscapeRisk", "inspect_browser_escape", "guard_browser_escape"]
