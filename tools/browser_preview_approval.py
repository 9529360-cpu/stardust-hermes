"""Shared high-risk action classification for browser and preview surfaces.

This is deliberately a policy seam, not a browser sandbox: ``browser_exec`` remains
arbitrary host-side automation and must not be represented as safely contained.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ActionRisk(StrEnum):
    SAFE = "safe"
    HIGH = "high"


@dataclass(frozen=True)
class ActionRiskDecision:
    risk: ActionRisk
    approval_key: str
    reason: str

    @property
    def requires_approval(self) -> bool:
        return self.risk is ActionRisk.HIGH


_READ_ONLY_PREVIEW = frozenset({"elements", "hover", "scroll", "strobe"})
_NAV_PREVIEW = frozenset({"back", "forward", "reload"})


def classify_browser_preview_action(surface: str, action: str, args: Mapping[str, Any] | None = None) -> ActionRiskDecision:
    """Return one shared, fail-closed risk decision for browser/Preview actions.

    Read-only inspection and visual navigation are safe. Any unknown surface or
    verb is high risk, as are Preview clicks, typing (including submit), keypresses,
    and browser automation. The caller still owns the actual approval transport.
    """
    normalized_surface = str(surface or "").strip().lower()
    normalized_action = str(action or "").strip().lower()
    if normalized_surface == "preview" and normalized_action in _READ_ONLY_PREVIEW | _NAV_PREVIEW:
        return ActionRiskDecision(ActionRisk.SAFE, f"{normalized_surface}:{normalized_action}", "read-only or navigation action")
    if normalized_surface == "preview":
        return ActionRiskDecision(ActionRisk.HIGH, f"{normalized_surface}:{normalized_action or 'unknown'}", "Preview can mutate the guest page")
    if normalized_surface == "browser":
        return ActionRiskDecision(ActionRisk.HIGH, f"{normalized_surface}:{normalized_action or 'automation'}", "browser automation is not sandboxed")
    return ActionRiskDecision(ActionRisk.HIGH, f"{normalized_surface or 'unknown'}:{normalized_action or 'unknown'}", "unknown action surface")
