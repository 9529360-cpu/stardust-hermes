"""Read-only aggregation and wire shaping for the Desktop Task Center."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from agent.assistant_intent import (
    AssistantTaskProjection,
    AssistantTaskState,
    project_cron_job,
    project_cron_run,
    project_delegation,
    project_goal,
    summarize_line,
)
from hermes_constants import get_hermes_home, profile_name_for_home

_MAX_LIMIT = 50
_MAX_TEXT = 500
# Owner IDs participate in navigation and must remain byte-for-byte stable when valid.
# Restrict them to printable URL-safe ASCII and bound their size; opaque IDs live in a
# disjoint namespace and are derived from the full source value to avoid lossy cleanup.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
_OPAQUE_PREFIX = "opaque-sha256:"


def _safe_identifier(value: Any, *, allow_opaque: bool = False) -> str | None:
    text = str(value or "")
    if _SAFE_ID.fullmatch(text) and summarize_line(text, 160) == text:
        return text
    if not allow_opaque or not text:
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
    return _OPAQUE_PREFIX + digest


def _safe_row(projection: AssistantTaskProjection, profile: str) -> dict[str, Any]:
    """Build the public allowlist, sanitizing every user-controlled text field at this boundary."""
    return {
        "task_id": _safe_identifier(projection.task_id, allow_opaque=True),
        "title": summarize_line(projection.title, _MAX_TEXT),
        "state": projection.state.value,
        "rail": projection.rail.value,
        "durability": projection.durability,
        "background": projection.background,
        "phase": summarize_line(projection.phase, 80) or None,
        "started_at": projection.started_at,
        "updated_at": projection.updated_at,
        "next_run_at": projection.next_run_at,
        "progress_current": projection.progress_current,
        "progress_total": projection.progress_total,
        "parent_session_id": _safe_identifier(projection.parent_session_id),
        "parent_task_id": _safe_identifier(projection.parent_task_id),
        "requires_approval": projection.requires_approval,
        "recoverable": projection.recoverable,
        "recovery_action": projection.recovery_action,
        "summary": summarize_line(projection.summary, 160),
        "detail": summarize_line(projection.detail, _MAX_TEXT),
        "files": [summarize_line(path, 1000) for path in projection.files[:8]],
        "needs_attention": projection.needs_attention,
        "terminal": projection.terminal,
        "profile": profile or None,
    }


def _read_delegations(limit: int) -> tuple[list[dict[str, Any]], bool]:
    from hermes_constants import hermes_home_key
    from tools.async_delegation_view import peek_durable_delegations

    owner_home = hermes_home_key()
    records: dict[str, dict[str, Any]] = {}
    degraded = False
    try:
        for row in peek_durable_delegations(limit=limit):
            records[str(row.get("delegation_id") or "")] = row
    except Exception:
        degraded = True

    projections: list[dict[str, Any]] = []
    for row in records.values():
        try:
            projection = project_delegation(row)
            projections.append(projection)
        except (TypeError, ValueError):
            degraded = True
    return projections, degraded


def _read_cron(limit: int) -> tuple[list[AssistantTaskProjection], bool]:
    from cron.jobs import peek_jobs
    from cron.executions import execution_owner_abandoned, peek_executions

    projections: list[AssistantTaskProjection] = []
    degraded = False
    jobs: list[dict[str, Any]] = []
    try:
        jobs = peek_jobs(include_disabled=True)
    except Exception:
        degraded = True
    executions: list[dict[str, Any]] = []
    try:
        executions = peek_executions(limit=limit)
    except Exception:
        degraded = True
    names = {str(job.get("id") or ""): str(job.get("name") or "") for job in jobs}
    active_jobs: set[str] = set()
    for execution in executions:
        status = str(execution.get("status") or "")
        abandoned = False
        if status in {"claimed", "running"}:
            try:
                abandoned = execution_owner_abandoned(execution)
            except Exception:
                degraded = True
            if not abandoned:
                active_jobs.add(str(execution.get("job_id") or ""))
        try:
            projections.append(
                project_cron_run(
                    execution,
                    job_name=names.get(str(execution.get("job_id") or ""), ""),
                    owner_lost=abandoned,
                )
            )
        except (TypeError, ValueError):
            degraded = True

    for job in jobs[:limit]:
        job_id = str(job.get("id") or "")
        try:
            projections.append(project_cron_job(job, running=job_id in active_jobs))
        except (TypeError, ValueError):
            degraded = True
    return projections, degraded


def _read_goals(limit: int) -> tuple[list[AssistantTaskProjection], bool]:
    from hermes_cli.goals_view import list_goal_states

    try:
        rows = list_goal_states(limit=limit)
    except Exception:
        return [], True
    projections: list[AssistantTaskProjection] = []
    degraded = False
    for row in rows:
        try:
            projections.append(
                project_goal(
                    str(row.get("session_id") or ""),
                    row.get("goal") or {},
                    waiting=bool(row.get("waiting")),
                )
            )
        except (TypeError, ValueError):
            degraded = True
    return projections, degraded


def list_task_rows(*, limit: int = 20) -> dict[str, Any]:
    """Aggregate profile-owned task rows with per-rail degradation and a strict wire allowlist."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    profile_name = profile_name_for_home(get_hermes_home())
    profile = "" if profile_name in (None, "default") else str(profile_name)
    projections: list[AssistantTaskProjection] = []
    degraded: list[str] = []

    for rail, reader in (
        ("delegation", lambda: _read_delegations(limit)),
        ("cron", lambda: _read_cron(limit)),
        ("goal", lambda: _read_goals(limit)),
    ):
        try:
            rows, failed = reader()
        except Exception:
            rows, failed = [], True
        projections.extend(rows)
        if failed:
            degraded.append(rail)

    rows = [_safe_row(row, profile) for row in projections]
    rows.sort(
        key=lambda row: (row["updated_at"] or row["started_at"] or 0, row["task_id"]),
        reverse=True,
    )
    return {"tasks": rows[: limit * 3], "degraded": degraded}
