"""Unified profile-local work snapshots and control (no execution or delivery ownership).

IDs are kind-prefixed to avoid collisions. Timestamps are Unix seconds; detail is
an allowlisted observation, never a callable, routing context or agent object.
Cancellation of agents is cooperative: acceptance is not a terminal result.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from hermes_constants import hermes_home_key
from agent.redact import redact_sensitive_text
from tools import async_delegation
from tools.delegate_tool_registry import list_active_subagents, interrupt_subagent
from tools.process_registry import process_registry


logger = logging.getLogger(__name__)

_ACTIVE = {"running", "dispatched", "stalling", "finalizing", "queued"}


def _status(value: str) -> str:
    if value in _ACTIVE:
        return "running"
    if value in {"completed", "failed", "cancelled", "interrupted"}:
        return value
    if value in {"unknown", "stalled"}:
        return "interrupted"
    return "failed"


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return None


def subagent_work(record: dict) -> dict:
    started = _timestamp(record.get("started_at"))
    return {"id": f"subagent:{record['subagent_id']}", "kind": "subagent",
            "title": str(record.get("goal") or "Subagent"),
            "status": _status(record.get("status", "running")), "started_at": started,
            "updated_at": _timestamp(record.get("updated_at")) or started,
            "detail": {k: record[k] for k in ("model", "delegation_id", "last_tool", "tool_count") if k in record}}


def cron_work(record: dict) -> dict | None:
    """Normalize one durable cron execution for the internal work ledger. The title is the job id:
    a stored job name falls back to prompt text, so names never leave the server."""
    execution_id = record.get("id")
    if not execution_id:
        return None
    raw_status = record.get("status", "unknown")
    status = "running" if raw_status in {"claimed", "running"} else _status(raw_status)
    started = _timestamp(record.get("started_at") or record.get("claimed_at"))
    updated = _timestamp(record.get("finished_at") or record.get("started_at") or record.get("claimed_at"))
    detail = {
        key: record[key]
        for key in ("job_id", "source", "delivery_outcome")
        if key in record and record[key] is not None
    }
    if record.get("error") is not None:
        detail["error"] = redact_sensitive_text(
            str(record["error"]), force=True, redact_url_credentials=True
        )
    return {
        "id": f"cron:{execution_id}",
        "kind": "cron",
        "title": f"Cron job {record.get('job_id') or 'run'}",
        "status": status,
        "started_at": started,
        "updated_at": updated or started,
        "detail": detail,
    }


def cron_work_items(limit: int = 100) -> list[dict]:
    """Recent durable cron runs of the active profile home, newest first. Reads never create the ledger:
    an absent executions.db is an empty list, and an unreadable one is logged and also empty.
    Job names are not read at all, so this does not touch the jobs store."""
    from cron.executions import executions_db_exists, list_executions

    if not executions_db_exists():
        return []
    try:
        records = list_executions(limit=limit)
    except Exception:
        logger.warning("Work ledger skipped cron execution history", exc_info=True)
        return []
    items = (cron_work(record) for record in records)
    return [item for item in items if item is not None]


def list_work(*, include_subagents: bool = True, include_cron: bool = True) -> list[dict]:
    """List durable delegations, live/recent processes, cron runs and live subagents."""
    delegations = {r["delegation_id"]: r for r in async_delegation.list_durable_delegations()}
    for record in async_delegation.list_async_delegations():
        if record.get("owner_home") not in (None, "", hermes_home_key()):
            continue
        rid = record["delegation_id"]
        delegations[rid] = {**delegations.get(rid, {}), **record}
    work = []
    for rid, record in delegations.items():
        started = _timestamp(record.get("dispatched_at"))
        result = record.get("result") or {}
        work.append({"id": f"delegation:{rid}", "kind": "delegation",
                     "title": str(record.get("goal") or "; ".join(record.get("goals") or []) or "Delegation"),
                     "status": _status(record.get("status", "running")), "started_at": started,
                     "updated_at": _timestamp(record.get("updated_at") or record.get("completed_at")) or started,
                     "detail": {k: result[k] for k in ("summary", "error") if k in result}})
    for record in process_registry.list_sessions(include_retained=True):
        status = "running"
        if record["status"] != "running":
            status = ("cancelled" if record.get("completion_reason") == "killed" else
                      "completed" if record.get("exit_code") == 0 else "failed")
        started = _timestamp(record.get("started_at"))
        work.append({"id": f"process:{record['session_id']}", "kind": "process",
                     "title": record.get("command") or "Background process", "status": status,
                     "started_at": started, "updated_at": _timestamp(record.get("updated_at")) or started,
                     "detail": {k: record[k] for k in ("pid", "exit_code", "completion_reason", "output_preview") if k in record}})
    if include_cron:
        work.extend(cron_work_items())
    if include_subagents:
        work.extend(subagent_work(r) for r in list_active_subagents())
    return sorted(work, key=lambda r: (-(r["started_at"] or 0), r["id"]))


def cancel_work(id: str, *, include_subagents: bool = True) -> dict:
    """Cancel by ledger ID. Finished/unknown IDs are harmless and explicit."""
    record = next((r for r in list_work(include_subagents=include_subagents) if r["id"] == id), None)
    def result(status, message):
        return {"id": id, "status": status, "message": message}
    if record is None:
        return result("not_found", "Unknown work ID.")
    if record["status"] != "running":
        return result("already_finished", f"Work is already {record['status']}.")
    kind, raw_id = id.split(":", 1)
    if kind == "process":
        outcome = process_registry.kill_process(raw_id, source="work.cancel", consume_output=False)
        if outcome["status"] == "killed":
            return result("cancelled", "Background process killed.")
        if outcome["status"] == "already_exited":
            return result("already_finished", "Background process already exited.")
        return result("error", outcome.get("error", "Unable to kill background process."))
    if kind == "cron":
        return result("unavailable", "Cron execution history is durable but not cancellable after dispatch.")
    accepted = (async_delegation.interrupt_delegation(raw_id) if kind == "delegation"
                else interrupt_subagent(raw_id))
    return (result("interrupt_requested", "Interruption requested; awaiting worker completion.") if accepted
            else result("unavailable", "Work is no longer controllable by this process."))
