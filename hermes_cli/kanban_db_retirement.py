"""Archiving and deleting tasks together with their workers, attachments, logs and
workspaces.

Split out of ``hermes_cli.kanban_db``, which re-exports every name here. Names this
module does not define are reached late-bound via ``_kb`` (import-cycle breaking), so
monkeypatching ``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger("hermes_cli.kanban_db")  # log-record parity with the origin module

def archive_task(conn: sqlite3.Connection, task_id: str, *, signal_fn=None) -> bool:
    """Archive a task; a *running* task's host-local worker is terminated.

    Clearing ``worker_pid`` in the DB alone left the OS process running past its
    own archive — it kept executing (and pushing work) against a task nothing
    tracked anymore (#76196). Snapshot pid+claim inside the archive txn so the
    kill is contingent on THIS caller winning the archive transition (a losing
    concurrent archiver must never signal the pid); the kill itself runs after
    commit — ``_poll_worker_exit`` can wait ~5 s and must not hold the write
    lock. Post-release kill is safe here because ``archived`` is terminal: no
    dispatcher can spawn a duplicate worker off the released claim. The
    termination outcome lands as its own ``archive_worker_termination`` event so
    the ``archived`` event stays atomic with the status flip.
    """
    with _kb.write_txn(conn):
        row = conn.execute(
            "SELECT status, claim_lock, worker_pid, worker_started_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return False
        was_running = row["status"] == "running"
        prev_pid, prev_lock, prev_started = row["worker_pid"], row["claim_lock"], row["worker_started_at"]
        cur = conn.execute(
            "UPDATE tasks SET status = 'archived', "
            "    claim_lock = NULL, claim_expires = NULL, worker_pid = NULL "
            "WHERE id = ? AND status != 'archived'", (task_id,),
        )
        if cur.rowcount != 1:
            return False
        # Archived mid-run (dashboard): close the run so history isn't orphaned.
        run_id = _kb._end_run(
            conn, task_id, outcome="reclaimed", status="reclaimed",
            summary="task archived with run still active",
        )
        _kb._append_event(
            conn,
            task_id,
            "archived",
            {"was_running": was_running, "prev_pid": prev_pid if was_running else None},
            run_id=run_id,
        )
    if was_running:
        termination = _kb._terminate_reclaimed_worker(prev_pid, prev_lock, signal_fn=signal_fn, started_at=prev_started)
        with _kb.write_txn(conn):
            _kb._append_event(conn, task_id, "archive_worker_termination", termination, run_id=run_id)
        if not termination.get("terminated"):
            raise _kb.WorkerTerminationError(
                "worker stop was not confirmed after archive; task remains archived "
                "and will not be dispatched"
            )
    # ``archived`` parents no longer block children; promote them now.
    _kb.recompute_ready(conn)
    # Reap the workspace on archive too (never-completed tasks kept it forever).
    _kb._cleanup_workspace(conn, task_id)
    return True


def _delete_task_relations(conn: sqlite3.Connection, task_id: str) -> list[str]:
    """Delete every row referencing ``task_id``; return attachment blobs to unlink.

    The schema intentionally has no ON DELETE CASCADE. Keep this list complete so a
    hard delete cannot leave durable rows (or attachment files) behind.
    """
    attachment_paths = [
        str(row["stored_path"])
        for row in conn.execute(
            "SELECT stored_path FROM task_attachments WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        if row["stored_path"]
    ]
    conn.execute("DELETE FROM task_links WHERE parent_id = ? OR child_id = ?", (task_id, task_id))
    for table in (
        "task_comments",
        "task_events",
        "task_runs",
        "task_attachments",
        "kanban_notify_subs",
    ):
        conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
    return attachment_paths


def _managed_attachment_path(task_id: str, raw_path: str) -> Optional[Path]:
    """Return a managed attachment blob path, else None.

    stored_path is persisted data and may be stale/tampered or originate
    from an old direct domain caller. Never unlink an arbitrary host path merely
    because a DB row points at it. A managed blob must live under one of the
    Kanban attachment roots and under that task's own directory.
    """
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    roots: list[Path] = []
    override = os.environ.get("HERMES_KANBAN_ATTACHMENTS_ROOT", "").strip()
    if override:
        with contextlib.suppress(OSError, RuntimeError):
            roots.append(Path(override).expanduser().resolve(strict=False))

    try:
        home = _kb.kanban_home().expanduser().resolve(strict=False)
        roots.append((home / "kanban" / "attachments").resolve(strict=False))
        named_root = home / "kanban" / "boards"
        if named_root.is_dir():
            for child in named_root.iterdir():
                if child.is_dir():
                    roots.append((child / "attachments").resolve(strict=False))
    except (OSError, RuntimeError):
        pass

    for root in roots:
        try:
            rel = candidate.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) >= 2 and rel.parts[0] == task_id:
            return candidate
    return None


def _unlink_deleted_attachment_files(task_id: str, paths: Iterable[str]) -> None:
    """Best-effort cleanup of blobs owned by this task's managed attachment dir."""
    for raw_path in paths:
        path = _managed_attachment_path(task_id, raw_path)
        if path is None:
            continue
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()

def _unlink_deleted_worker_logs(task_id: str, board: Optional[str]) -> None:
    """Remove the task-owned log and rotated backups from the correct board."""
    log_path = _kb.worker_log_path(task_id, board=board)
    candidates = [log_path]
    with contextlib.suppress(OSError):
        candidates.extend(log_path.parent.glob(log_path.name + ".*"))
    for path in candidates:
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()

def _hard_delete_workspace_blocker(conn: sqlite3.Connection, task_id: str) -> Optional[str]:
    """Return a task-owned workspace path that still needs recovery/cleanup."""
    row = conn.execute(
        "SELECT workspace_kind, workspace_path FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None or not row["workspace_path"]:
        return None

    kind = str(row["workspace_kind"] or "")
    path = Path(str(row["workspace_path"])).expanduser()
    try:
        exists = path.exists()
    except OSError:
        exists = True
    if not exists:
        return None

    if kind == "worktree":
        return str(path)
    if kind == "scratch":
        try:
            from hermes_cli import kanban_db_workspace as kbw

            if kbw._is_managed_scratch_path(path):
                return str(path)
        except Exception:
            # Failure to prove ownership is a reason not to remove the user's
            # task record while a supposedly managed scratch path still exists.
            return str(path)
    return None

def delete_archived_task(
    conn: sqlite3.Connection, task_id: str, *, board: Optional[str] = None,
) -> bool:
    """Hard-delete an ARCHIVED task only after any displaced worker stop settled."""
    if _kb._task_status(conn, task_id) != "archived":
        return False
    unsafe = _archive_worker_stop_unverified(conn, task_id)
    if unsafe is not None:
        raise RuntimeError(
            "cannot permanently delete task because its worker stop was not confirmed; "
            "the task remains archived"
        )

    workspace_blocker = _hard_delete_workspace_blocker(conn, task_id)
    if workspace_blocker is not None:
        raise RuntimeError(
            "cannot permanently delete task because its task-owned workspace is still present; "
            "the task remains archived for recovery"
        )

    attachment_paths: list[str] = []
    with _kb.write_txn(conn):
        # Re-check under the write lock so a concurrent restore/reopen cannot
        # cross the destructive boundary after the safety check above.
        if _kb._task_status(conn, task_id) != "archived":
            return False
        attachment_paths = _delete_task_relations(conn, task_id)
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        deleted = cur.rowcount == 1
        if deleted:
            # Hard delete removes the task's own append-only history. Leave one
            # board-scoped mutation row so websocket cursors advance and other
            # open clients refresh immediately without retaining task history.
            _kb._append_event(conn, "", "task_deleted", {"task_id": task_id})
    if deleted:
        _unlink_deleted_attachment_files(task_id, attachment_paths)
        _unlink_deleted_worker_logs(task_id, board)
        _kb.recompute_ready(conn)
    return deleted


def _archive_worker_stop_unverified(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    """Return evidence that the latest archive still has an unsettled worker stop.

    New archive events carry was_running so a concurrent purge cannot slip
    between the durable archive transition and the post-commit process kill.
    Legacy archive events lacked that marker; when they do have a subsequent
    termination event, still honor an explicit terminated=false result.
    """
    archived = conn.execute(
        "SELECT id, payload FROM task_events WHERE task_id = ? AND kind = 'archived' "
        "ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if archived is None:
        return None
    archived_payload = _kb._json_dict(archived["payload"])

    row = conn.execute(
        "SELECT payload FROM task_events "
        "WHERE task_id = ? AND kind = 'archive_worker_termination' AND id > ? "
        "ORDER BY id ASC LIMIT 1",
        (task_id, int(archived["id"])),
    ).fetchone()
    if row is None:
        if archived_payload.get("was_running"):
            return {
                "prev_pid": archived_payload.get("prev_pid"),
                "termination_pending": True,
                "terminated": False,
            }
        return None

    payload = _kb._json_dict(row["payload"])
    if payload.get("prev_pid") and not payload.get("terminated"):
        return payload
    return None

def delete_task(
    conn: sqlite3.Connection, task_id: str, *, signal_fn=None, board: Optional[str] = None,
) -> bool:
    """Lifecycle-safe hard delete.

    Active work is archived first so its worker is stopped through the normal
    lifecycle. Hard deletion is refused when a known worker could not be
    confirmed stopped; the archived record is deliberately retained for
    recovery/diagnostics instead of creating an invisible orphan process.
    """
    task = _kb.get_task(conn, task_id)
    if task is None:
        return False
    if task.status != "archived":
        # Do not treat a concurrent archive as our successful stop boundary:
        # another caller may still be between the durable status flip and the
        # post-commit worker termination. Refuse and let the user retry.
        if not archive_task(conn, task_id, signal_fn=signal_fn):
            return False

    return delete_archived_task(conn, task_id, board=board)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``kanban_db`` re-exports from it.
from hermes_cli import kanban_db as _kb  # noqa: E402
