"""Product-level state projection for durable personal-assistant work.

The scheduler's internal statuses are intentionally richer and older than the Personal AI Agent UX.
This module is the translation boundary: callers may keep using the existing durable task kernel while
Desktop/model-facing surfaces reason in terms of queued/running/waiting/completed/failed/cancelled.
It owns no state and performs no mutation.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


PUBLIC_STATES = frozenset({
    "queued",
    "scheduled",
    "running",
    "waiting_dependency",
    "waiting_confirmation",
    "waiting_input",
    "waiting_review",
    "needs_attention",
    "completed",
    "failed",
    "cancelled",
})


def _event_values(events: Iterable[Any]) -> list[Mapping[str, Any]]:
    return [event for event in events if isinstance(event, Mapping)]


def _latest_lifecycle_event(events: Iterable[Any]) -> Mapping[str, Any] | None:
    interesting = {"blocked", "unblocked", "gave_up", "completed", "archived", "status"}
    for event in reversed(_event_values(events)):
        if str(event.get("kind") or "") in interesting:
            return event
    return None


def _blocked_payload(events: Iterable[Any]) -> Mapping[str, Any]:
    for event in reversed(_event_values(events)):
        if str(event.get("kind") or "") != "blocked":
            continue
        payload = event.get("payload")
        return payload if isinstance(payload, Mapping) else {}
    return {}


def project_background_state(
    kernel_status: str,
    *,
    dependencies: Iterable[Any] = (),
    pending_approvals: Iterable[Any] = (),
    events: Iterable[Any] = (),
    last_failure_error: str | None = None,
) -> str:
    """Translate one kernel task snapshot into a stable user-facing state."""
    status = str(kernel_status or "").strip().lower()
    deps = list(dependencies or ())
    approvals = list(pending_approvals or ())
    event_list = _event_values(events)

    if status == "done":
        return "completed"
    if status == "archived":
        return "cancelled"
    if status == "running":
        return "running"
    if status == "review":
        return "waiting_review"
    if status == "scheduled":
        return "scheduled"
    if status == "ready":
        return "queued"
    if status == "todo":
        return "waiting_dependency" if deps else "queued"
    if status == "triage":
        return "needs_attention"

    if status == "blocked":
        if approvals:
            return "waiting_confirmation"

        latest = _latest_lifecycle_event(event_list)
        if latest and str(latest.get("kind") or "") == "gave_up":
            return "failed"

        payload = _blocked_payload(event_list)
        reason = str(payload.get("reason") or "")
        block_kind = str(payload.get("kind") or payload.get("block_kind") or "").strip().lower()
        reason_lower = reason.lower()
        if "approval" in reason_lower and ("user" in reason_lower or "confirm" in reason_lower):
            return "waiting_confirmation"
        if block_kind == "dependency":
            return "waiting_dependency"
        if block_kind == "needs_input":
            return "waiting_input"
        if block_kind == "capability":
            return "needs_attention"
        if block_kind == "transient":
            # The kernel has stopped automatic execution at this point; describe what the user needs
            # to know rather than pretending an automatic retry is currently active.
            return "needs_attention"
        if last_failure_error:
            return "needs_attention"
        return "waiting_input"

    # Unknown/new kernel phases fail toward a non-terminal, visible state instead of falsely claiming
    # completion or failure. This also keeps mixed-version clients safe during additive kernel changes.
    return "needs_attention"


def state_flags(state: str) -> dict[str, bool]:
    """Small UI/model convenience flags derived from the single public state authority."""
    value = state if state in PUBLIC_STATES else "needs_attention"
    return {
        "terminal": value in {"completed", "failed", "cancelled"},
        "waiting": value.startswith("waiting_") or value == "needs_attention",
        "needs_user": value in {"waiting_confirmation", "waiting_input", "needs_attention"},
    }
