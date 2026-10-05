"""Claims: ``ready -> running`` and review-lane claims, the run a claim opens, claim
heartbeats, stale-claim release, and manual reclaim / reassign.

Split out of ``hermes_cli.kanban_db``, which re-exports every name here. Names this
module does not define are reached late-bound via ``_kb`` (import-cycle breaking), so
monkeypatching ``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Optional

_log = logging.getLogger("hermes_cli.kanban_db")  # log-record parity with the origin module

# --- Claim / complete / block ---

def _parents_satisfied(conn: sqlite3.Connection, task_id: str) -> bool:
    """Return whether every direct parent is terminal for dependency gating."""
    return conn.execute(
        # Check if this task has children that still need the workspace. If any child is not yet
        # done/archived, defer cleanup so the child can read handoff artifacts from the workspace (#33774).
        "SELECT 1 FROM task_links l "
        "JOIN tasks p ON p.id = l.parent_id "
        "WHERE l.child_id = ? "
        "AND p.status NOT IN ('done', 'archived') LIMIT 1", (task_id,),
    ).fetchone() is None


def _claim_and_open_run(
    conn: sqlite3.Connection, task_id: str, source_status: str, lock: str, expires: int, now: int,
    *, event_extra: Optional[dict] = None,
) -> Optional[int]:
    """CAS ``source_status -> running``, open a run row, emit ``claimed``; None
    when the CAS lost. Caller holds the txn."""
    cur = conn.execute(
        f"""
        UPDATE tasks
           SET status        = 'running',
               claim_lock    = ?,
               claim_expires = ?,
               started_at    = COALESCE(started_at, ?)
         WHERE id = ?
           AND status = '{source_status}'
           AND claim_lock IS NULL
        """,
        (lock, expires, now, task_id),
    )
    if cur.rowcount != 1:
        return None
    trow = conn.execute(
        "SELECT assignee, max_runtime_seconds, current_step_key "
        "FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    run_cur = conn.execute(
        """
        INSERT INTO task_runs (
            task_id, profile, step_key, status,
            claim_lock, claim_expires, max_runtime_seconds,
            started_at
        ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
        """,
        (
            task_id, trow["assignee"] if trow else None, trow["current_step_key"] if trow else None,
            lock, expires, trow["max_runtime_seconds"] if trow else None, now,
        ),
    )
    run_id = run_cur.lastrowid
    conn.execute("UPDATE tasks SET current_run_id = ? WHERE id = ?", (run_id, task_id))
    _kb._append_event(
        conn, task_id, "claimed",
        {"lock": lock, "expires": expires, "run_id": run_id, **(event_extra or {})}, run_id=run_id,
    )
    return run_id


def claim_task(
    conn: sqlite3.Connection, task_id: str, *, ttl_seconds: Optional[int] = None,
    claimer: Optional[str] = None,
) -> Optional[_kb.Task]:
    """Atomically transition ``ready -> running``.

    Returns the claimed ``Task`` on success, ``None`` if the task was
    already claimed (or is not in ``ready`` status).
    """
    now = int(time.time())
    lock = claimer or _kb._claimer_id()
    expires = now + _kb._resolve_claim_ttl_seconds(ttl_seconds)
    with _kb.write_txn(conn):
        # Single enforcement point: never ready -> running with an undone
        # parent, whichever writer set 'ready'. Demote to 'todo';
        # recompute_ready re-promotes when the parents finish.
        if not _parents_satisfied(conn, task_id):
            conn.execute(
                "UPDATE tasks SET status = 'todo' "
                "WHERE id = ? AND status = 'ready'", (task_id,),
            )
            _kb._append_event(conn, task_id, "claim_rejected", {"reason": "parents_not_done"})
            return None
        # Close a leaked prior run so the CAS below doesn't strand it.
        _kb._reclaim_dangling_run(
            conn, task_id, statuses=("ready",), now=now, note="invariant recovery on re-claim",
        )
        run_id = _claim_and_open_run(conn, task_id, "ready", lock, expires, now)
        if run_id is None:
            return None
        claimed = _kb.get_task(conn, task_id)
    _kb._fire_task_hook("kanban_task_claimed", claimed, task_id, run_id)
    return claimed


def claim_review_task(
    conn: sqlite3.Connection, task_id: str, *, ttl_seconds: Optional[int] = None,
    claimer: Optional[str] = None,
) -> Optional[_kb.Task]:
    """Atomic ``review -> running`` (None when lost). Parents are re-checked
    (one may have reopened meanwhile) and a NEW run tracks the reviewer
    separately from the implementer."""
    now = int(time.time())
    lock = claimer or _kb._claimer_id()
    expires = now + _kb._resolve_claim_ttl_seconds(ttl_seconds)
    with _kb.write_txn(conn):
        if not _parents_satisfied(conn, task_id):
            demoted = conn.execute(
                "UPDATE tasks SET status = 'todo' "
                "WHERE id = ? AND status = 'review' AND claim_lock IS NULL", (task_id,),
            )
            if demoted.rowcount == 1:
                _kb._append_event(
                    conn, task_id, "dependency_wait",
                    {"reason": "parent_reopened", "source_status": "review"},
                )
            return None
        run_id = _claim_and_open_run(
            conn, task_id, "review", lock, expires, now, event_extra={"source_status": "review"},
        )
        if run_id is None:
            return None
        return _kb.get_task(conn, task_id)


def _retry_status_for_run(
    conn: sqlite3.Connection, task_id: str, run_id: Optional[int] = None,
) -> str:
    """``review`` when the run's ``claimed`` event says ``source_status=review``,
    else ``ready`` — one place, so crash/timeout/reclaim can't silently turn a
    reviewer run into an implementation run."""
    if run_id is None:
        run_id = _kb._current_run_id(conn, task_id)
    if run_id is None:
        return "ready"
    event = _kb._latest_event(conn, task_id, "claimed", run_id)
    payload = _kb._json_dict(_kb._row_get(event, "payload"))
    return "review" if payload.get("source_status") == "review" else "ready"


# Run outcome -> lifecycle status a goal loop should report for a handed-off run.
_RUN_OUTCOME_TERMINAL_STATUS = {
    "completed": "done",
    "review_requested": "review",
    "changes_requested": "changes_requested",
    "blocked": "blocked",
    "dependency_wait": "blocked",
}


def goal_run_status(
    conn: sqlite3.Connection, task_id: str, expected_run_id: Optional[int] = None,
) -> Optional[str]:
    """Lifecycle status as seen by ONE run: terminal handoffs bind to that run,
    any other ownership loss is ``superseded`` — otherwise an old goal loop
    would read the successor's live ``running`` and mutate it."""
    task = _kb.get_task(conn, task_id)
    if task is None:
        return None
    if expected_run_id is not None:
        row = conn.execute(
            "SELECT outcome FROM task_runs WHERE id = ? AND task_id = ?",
            (int(expected_run_id), task_id),
        ).fetchone()
        outcome = str(row["outcome"]) if row and row["outcome"] is not None else None
        terminal_status = _RUN_OUTCOME_TERMINAL_STATUS.get(outcome)
        if terminal_status is not None:
            return terminal_status
        if outcome is not None or task.current_run_id != int(expected_run_id):
            return "superseded"
    if task.status in {"ready", "todo"}:
        event = conn.execute(
            "SELECT kind FROM task_events WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1", (task_id,),
        ).fetchone()
        if event and event["kind"] == "changes_requested":
            return "changes_requested"
    return task.status


def heartbeat_claim(
    conn: sqlite3.Connection, task_id: str, *, ttl_seconds: Optional[int] = None,
    claimer: Optional[str] = None,
) -> bool:
    """Extend a running claim; True if we still own it."""
    expires = int(time.time()) + _kb._resolve_claim_ttl_seconds(ttl_seconds)
    lock = claimer or _kb._claimer_id()
    with _kb.write_txn(conn):
        cur = conn.execute(
            "UPDATE tasks SET claim_expires = ? "
            "WHERE id = ? AND status = 'running' AND claim_lock = ?", (expires, task_id, lock),
        )
        if cur.rowcount != 1:
            return False
        _extend_run_claim(conn, task_id, expires)
        return True


def _extend_run_claim(conn: sqlite3.Connection, task_id: str, expires: int) -> Optional[int]:
    """Mirror a task claim extension onto its active run row; returns that run id."""
    run_id = _kb._current_run_id(conn, task_id)
    if run_id is not None:
        conn.execute("UPDATE task_runs SET claim_expires = ? WHERE id = ?", (expires, run_id))
    return run_id


def release_stale_claims(
    conn: sqlite3.Connection, *, signal_fn=None, failure_limit: Optional[int] = None,
) -> int:
    """Reclaim ``running`` tasks whose claim expired; returns the count reclaimed.

    Every reclaim that actually releases a claim is a non-success attempt and
    is booked through ``_record_task_failure`` (#111306): a claim that expired
    without a worker ever spawning otherwise loops claim -> reclaim -> claim
    with ``consecutive_failures`` stuck at 0, so the breaker never trips.
    ``reclaim_task`` (operator path) deliberately resets the counter instead.

    A host-local worker that is still alive gets its claim *extended* instead
    (a slow model can sit longer than the TTL inside one tool-free call, so no
    heartbeat) — unless ``last_heartbeat_at`` is older than
    ``DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS`` (wedged; ``_touch_activity``
    keeps any genuinely active worker fresh). Safe to call often.

    Reclaiming a live worker mid-flight produces the spawn- then-immediately-reclaim loop seen on slow
    models that spend longer than ``DEFAULT_CLAIM_TTL_SECONDS`` inside a single tool-free LLM call (#23025):
    no tool calls means no ``kanban_heartbeat``, even though the subprocess is healthy.
    Backstop (#29747 gap 3): if the worker's PID is still alive but its ``last_heartbeat_at`` is stale by
    more than ``DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS`` (1h), the worker has been making no observable
    progress and we reclaim anyway — even if ``_pid_alive`` is still true. This catches the
    wedged-in-a-logic-loop case where the process is technically running but accomplishing nothing.
    ``_touch_activity`` (run_agent.py) bridges chunk-level liveness into ``last_heartbeat_at`` via #31752,
    so any genuinely active worker keeps its heartbeat fresh as a side effect of normal API traffic.
    ``enforce_max_runtime`` and ``detect_crashed_workers`` remain the upper bounds for genuinely wedged or
    dead workers.
    """
    now = int(time.time())
    reclaimed = 0
    host_prefix = _kb._host_prefix()
    stale = conn.execute(
        "SELECT id, claim_lock, worker_pid, worker_started_at, claim_expires, last_heartbeat_at, "
        "       assignee "
        "FROM tasks "
        "WHERE status = 'running' AND claim_expires IS NOT NULL "
        "  AND claim_expires < ?", (now,),
    ).fetchall()
    for row in stale:
        host_local = (row["claim_lock"] or "").startswith(host_prefix)
        hb = row["last_heartbeat_at"]
        # Backstop: a heartbeat older than the max-stale threshold means no
        # observable progress — reclaim even if the PID is alive (logic loop).
        heartbeat_stale = hb is not None and (now - int(hb)) > _kb.DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS
        started_at = _kb._row_get(row, "worker_started_at")
        if (host_local and row["worker_pid"] and _kb._worker_alive(row["worker_pid"], started_at)
                and not heartbeat_stale):
            _extend_live_stale_claim(conn, row, now)
            continue

        termination = _kb._terminate_reclaimed_worker(
            row["worker_pid"], row["claim_lock"], signal_fn=signal_fn, started_at=started_at,
        )
        # A live worker of ours must keep its claim (else a duplicate spawns beside it).
        if _kb._worker_survived_termination(termination):
            _kb._defer_reclaim_for_live_worker(
                conn, row["id"], row["claim_lock"], now, termination,
                reason="ttl_expired_worker_alive",
            )
            continue
        with _kb.write_txn(conn):
            retry_status = _retry_status_for_run(conn, row["id"])
            cur = conn.execute(
                "UPDATE tasks SET status = ?, claim_lock = NULL, "
                "claim_expires = NULL, worker_pid = NULL "
                "WHERE id = ? AND status = 'running' AND claim_lock IS ? "
                "AND claim_expires IS NOT NULL AND claim_expires < ?",
                (retry_status, row["id"], row["claim_lock"], now),
            )
            if cur.rowcount != 1:
                continue
            run_id = _record_reclaim(
                conn, row["id"], termination,
                error=f"stale_lock={row['claim_lock']}",
                payload={
                    "stale_lock": row["claim_lock"],
                    "worker_pid": _kb._opt_int(row["worker_pid"]),
                    "claim_expires": int(row["claim_expires"]),
                    "last_heartbeat_at": _kb._opt_int(row["last_heartbeat_at"]),
                    "now": now,
                    "host_local": host_local,
                    "heartbeat_stale": bool(heartbeat_stale),
                    "retry_status": retry_status,
                },
            )
            reclaimed += 1
        # Own txn, after the reclaim commit (same shape as ``enforce_max_runtime``):
        # the run ended without a verdict, so it counts toward the breaker and a
        # trip flips the task to ``blocked`` + ``gave_up`` on top of ``reclaimed``.
        _kb._record_task_failure(
            conn, row["id"], f"stale_lock={row['claim_lock']}",
            outcome="reclaimed", failure_limit=failure_limit,
            release_claim=False, end_run=False,
            event_payload_extra={"worker_pid": _kb._opt_int(row["worker_pid"]), "retry_status": retry_status},
        )
        # Post-commit observer; every non-reclaim branch ``continue``d above.
        if _kb._kanban_observer_consumed("on_kanban_worker_stale_claim"):
            _kb._fire_kanban_lifecycle_hook(
                "on_kanban_worker_stale_claim", row["id"], board=_kb.get_current_board(),
                assignee=row["assignee"], run_id=run_id, worker_pid=_kb._opt_int(row["worker_pid"]),
                heartbeat_stale=bool(heartbeat_stale), retry_status=retry_status,
            )
    return reclaimed


def _record_reclaim(
    conn: sqlite3.Connection, task_id: str, termination: dict, *, error: str, payload: dict,
) -> Optional[int]:
    """Close the active run as ``reclaimed`` and emit the ``reclaimed`` event
    (payload merged with the termination report). Caller holds the txn."""
    run_id = _kb._end_run(
        conn, task_id, outcome="reclaimed", status="reclaimed", error=error, metadata=termination,
    )
    payload.update(termination)
    _kb._append_event(conn, task_id, "reclaimed", payload, run_id=run_id)
    return run_id


def _extend_live_stale_claim(conn: sqlite3.Connection, row: sqlite3.Row, now: int) -> None:
    """TTL-expired claim whose host-local worker is alive: extend instead of
    reclaiming (``claim_extended`` event). CAS on the same expired lock so a
    concurrent reclaimer wins cleanly."""
    new_expires = now + _kb._resolve_claim_ttl_seconds()
    with _kb.write_txn(conn):
        cur = conn.execute(
            "UPDATE tasks SET claim_expires = ? "
            "WHERE id = ? AND status = 'running' "
            "  AND claim_lock IS ? "
            "  AND claim_expires IS NOT NULL "
            "  AND claim_expires < ?", (new_expires, row["id"], row["claim_lock"], now),
        )
        if cur.rowcount != 1:
            return
        run_id = _extend_run_claim(conn, row["id"], new_expires)
        _kb._append_event(
            conn, row["id"], "claim_extended",
            {
                "reason": "pid_alive",
                "worker_pid": int(row["worker_pid"]),
                "claim_lock": row["claim_lock"],
                "claim_expires_was": int(row["claim_expires"]),
                "claim_expires_now": new_expires,
                "last_heartbeat_at": _kb._opt_int(row["last_heartbeat_at"]),
            },
            run_id=run_id,
        )


def reclaim_task(
    conn: sqlite3.Connection, task_id: str, *, reason: Optional[str] = None, signal_fn=None,
) -> bool:
    """Operator reclaim regardless of TTL: release the claim, restore the source
    phase, reset the failure counter. False when not running."""
    row = conn.execute(
        "SELECT status, claim_lock, worker_pid, worker_started_at FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    if not row:
        return False
    if row["status"] != "running" and row["claim_lock"] is None:
        # Nothing to reclaim — already ready / blocked / done.
        return False
    prev_lock = row["claim_lock"]
    termination = _kb._terminate_reclaimed_worker(
        row["worker_pid"], prev_lock, signal_fn=signal_fn, started_at=row["worker_started_at"])
    with _kb.write_txn(conn):
        retry_status = _retry_status_for_run(conn, task_id)
        cur = conn.execute(
            "UPDATE tasks SET status = ?, claim_lock = NULL, "
            "claim_expires = NULL, worker_pid = NULL "
            "WHERE id = ? AND status IN ('running', 'ready', 'blocked') "
            "AND claim_lock IS ?", (retry_status, task_id, prev_lock),
        )
        if cur.rowcount != 1:
            return False
        _record_reclaim(
            conn, task_id, termination,
            error=f"manual_reclaim: {reason}" if reason else f"manual_reclaim lock={prev_lock}",
            payload={"manual": True, "reason": reason, "prev_lock": prev_lock, "retry_status": retry_status},
        )
    # Operator intervention = fresh retry budget (own txn, runs after commit).
    _kb._clear_failure_counter(conn, task_id)
    return True


def reassign_task(
    conn: sqlite3.Connection, task_id: str, profile: Optional[str], *, reclaim_first: bool = False,
    reason: Optional[str] = None,
) -> bool:
    """Reassign (None unassigns); a running task is refused unless
    ``reclaim_first`` releases its claim — the "this profile's model is broken" path."""
    if reclaim_first:
        # Safe to call even if nothing to reclaim.
        reclaim_task(conn, task_id, reason=reason or "reassign")
    # assign_task handles its own txn + the still-running guard.
    try:
        return _kb.assign_task(conn, task_id, profile)
    except RuntimeError:
        # Task is still running and reclaim_first was False; caller
        # needs to decide whether to retry with reclaim.
        return False


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``kanban_db`` re-exports from it.
from hermes_cli import kanban_db as _kb  # noqa: E402
