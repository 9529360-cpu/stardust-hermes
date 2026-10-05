"""A kanban worker's own account of why its run failed.

The dispatcher sees only a dead worker's exit status ("pid 1234 exited with
code 1"); the reason — the model is not served, a quota wall, the context
overflowed — is known only inside the worker and used to end up solely in its
log file. So a crash notification, the task's ``last_failure_error``, the
retry worker's prior-attempt context and the assistant's task listing all said
"exited with code 1", and nobody could answer "why does the background task
keep failing?".

A failing worker records the reason on its own run (a ``worker_failed`` event)
right before it exits; when the dispatcher books the dead worker it folds the
reason into the run error, the crash/rate-limit event payload and the failure
counter's error text.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

WORKER_FAILED_EVENT = "worker_failed"
_MAX_ERROR_CHARS = 300


def _failure_summary(result: dict) -> dict:
    from agent.redact import redact_sensitive_text

    lines = str(result.get("error") or result.get("final_response") or "").strip().splitlines()
    error = redact_sensitive_text(lines[0] if lines else "", force=True, redact_url_credentials=True).strip()
    if len(error) > _MAX_ERROR_CHARS:
        error = error[: _MAX_ERROR_CHARS - 1] + "…"
    return {"failure_reason": str(result.get("failure_reason") or "") or None, "error": error or None}


def record_worker_failure(result: Any) -> None:
    """Record a failed turn ``result`` on this dispatcher-spawned worker's run.

    Called just before the worker exits non-zero. Only the run the dispatcher
    gave this process (``HERMES_KANBAN_RUN_ID``) is annotated, and only while it
    is still that task's live run. Never raises: the exit status must reach the
    dispatcher even when the board cannot be written.
    """
    task_id = os.environ.get("HERMES_KANBAN_TASK") or ""
    raw_run_id = os.environ.get("HERMES_KANBAN_RUN_ID") or ""
    if not task_id or not raw_run_id.isdigit() or not isinstance(result, dict):
        return
    from agent.delegation_context import is_dispatcher_owned_worker_context

    if not is_dispatcher_owned_worker_context():
        return
    try:
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_connect as kbc

        payload = _failure_summary(result)
        with kbc.connect_closing() as conn, kb.write_txn(conn):
            live = conn.execute(
                "SELECT 1 FROM tasks WHERE id = ? AND status = 'running' AND current_run_id = ?",
                (task_id, int(raw_run_id)),
            ).fetchone()
            if live is not None:
                kb._append_event(conn, task_id, WORKER_FAILED_EVENT, payload, run_id=int(raw_run_id))
    except Exception:
        logger.warning("kanban worker: could not record the failure reason on run %s", raw_run_id, exc_info=True)


def worker_failure_note(conn: sqlite3.Connection, task_id: str, run_id: Optional[int]) -> Optional[dict]:
    """The failure the worker recorded on ``run_id``, or None (dispatcher side)."""
    if run_id is None:
        return None
    from hermes_cli import kanban_db as kb

    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND run_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (task_id, int(run_id), WORKER_FAILED_EVENT),
    ).fetchone()
    note = kb._json_dict(row["payload"]) if row else {}
    return note if note.get("error") or note.get("failure_reason") else None


def describe_worker_failure(note: dict) -> str:
    """One line for humans and retry workers: ``model_not_found: Model 'x' isn't available …``."""
    reason, error = note.get("failure_reason"), note.get("error")
    return f"{reason}: {error}" if reason and error else str(error or reason)
