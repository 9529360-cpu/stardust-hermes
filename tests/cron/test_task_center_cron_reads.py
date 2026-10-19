"""Read-only cron entry points the task center relies on.

``list_jobs`` is not safe for a viewer: ``load_jobs`` creates ``cron/`` and ``cron/output`` and rewrites
a non-canonical ``jobs.json`` in place, and ``latest_executions`` creates ``executions.db``. ``peek_jobs``
and ``execution_owner_abandoned`` are the pure counterparts.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

import cron.executions as executions
from cron.jobs import list_jobs, peek_jobs, use_cron_store
from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override


def _job(job_id: str = "j1", **extra) -> dict:
    return {
        "id": job_id,
        "name": f"Job {job_id}",
        "prompt": "Summarize the inbox",
        "enabled": True,
        "state": "scheduled",
        "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
        "schedule_display": "every 60m",
        **extra,
    }


def _write(path: Path, payload) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.fixture()
def jobs_file() -> Path:
    return get_hermes_home() / "cron" / "jobs.json"


def test_peek_jobs_never_creates_store_files_or_output_directories():
    home = get_hermes_home()
    before = _tree(home)

    assert peek_jobs() == []

    assert not (home / "cron" / "jobs.json").exists()
    assert not (home / "cron" / "output").exists()
    assert _tree(home) == before


def test_peek_jobs_does_not_repair_or_create_anything(jobs_file):
    raw = _write(jobs_file, [_job("j1")])  # a bare list: load_jobs would rewrite this as {"jobs": [...]}
    cron_dir = jobs_file.parent

    jobs = peek_jobs()

    assert [job["id"] for job in jobs] == ["j1"]
    assert jobs[0]["state"] == "scheduled" and jobs[0]["name"] == "Job j1"
    assert jobs_file.read_bytes() == raw
    assert _tree(cron_dir) == {"jobs.json"}  # no executions.db, no output/ directory


def test_peek_jobs_matches_list_jobs_apart_from_the_ledger_join(jobs_file):
    _write(jobs_file, {"jobs": [_job("j1"), _job("j2", enabled=False, state="paused")]})

    peeked = peek_jobs()
    listed = [{k: v for k, v in job.items() if k != "latest_execution"} for job in list_jobs(include_disabled=True)]

    assert peeked == listed


def test_peek_jobs_flattens_an_id_map_in_memory_only(jobs_file):
    raw = _write(jobs_file, {"jobs": {"a": {"prompt": "x"}, "b": {"id": "named", "prompt": "y"}, "c": "junk"}})

    jobs = peek_jobs()

    assert sorted(job["id"] for job in jobs) == ["a", "named"]
    assert jobs_file.read_bytes() == raw


def test_peek_jobs_drops_non_dict_entries(jobs_file):
    _write(jobs_file, {"jobs": ["junk", 3, None, _job("j1")]})

    assert [job["id"] for job in peek_jobs()] == ["j1"]


@pytest.mark.parametrize("content", [b"{not json", b"", b'"a string"', b'{"jobs": 7}'])
def test_peek_jobs_reports_an_unreadable_store_without_touching_it(jobs_file, content):
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_bytes(content)
    before = hashlib.sha256(content).hexdigest()

    with pytest.raises(RuntimeError):
        peek_jobs()

    assert hashlib.sha256(jobs_file.read_bytes()).hexdigest() == before
    assert _tree(jobs_file.parent) == {"jobs.json"}


def test_peek_jobs_can_exclude_disabled_jobs(jobs_file):
    _write(jobs_file, {"jobs": [_job("on"), _job("off", enabled=False, state="paused")]})

    assert [job["id"] for job in peek_jobs()] == ["on", "off"]
    assert [job["id"] for job in peek_jobs(include_disabled=False)] == ["on"]


def test_peek_jobs_follows_the_profile_binding(tmp_path):
    other = tmp_path / "profiles" / "coder"
    _write(other / "cron" / "jobs.json", {"jobs": [_job("coder-job")]})

    token = set_hermes_home_override(other)
    try:
        assert [job["id"] for job in peek_jobs()] == ["coder-job"]
    finally:
        reset_hermes_home_override(token)
    assert peek_jobs() == []  # back in the default home

    with use_cron_store(other):
        assert [job["id"] for job in peek_jobs()] == ["coder-job"]


# ── execution_owner_abandoned ────────────────────────────────────────────────────────────────────

DEAD, LIVE, REUSED = 910_001, 910_002, 910_003


def _ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")
    monkeypatch.setattr(
        executions,
        "_owner_is_live",
        lambda pid, started: pid == LIVE or (pid == REUSED and started == 5),
    )
    return tmp_path / "cron" / "executions.db"


def test_owner_abandoned_agrees_with_what_recovery_rewrites(monkeypatch, tmp_path):
    db = _ledger(monkeypatch, tmp_path)
    ids = {name: executions.create_execution(name, source="builtin")["id"] for name in (
        "dead-claimed", "dead-running", "live-running", "reused-pid", "handoff-fresh", "handoff-stale",
        "own-process", "finished",
    )}
    executions.finish_execution(ids["finished"], success=True)
    grace = executions.HANDOFF_ADOPTION_GRACE_SECONDS
    now = time.time()
    with contextlib.closing(sqlite3.connect(db)) as conn, conn:
        for name, pid, status, started_at, pending, pending_since in (
            ("dead-claimed", DEAD, "claimed", None, 0, None),
            ("dead-running", DEAD, "running", None, 0, None),
            ("live-running", LIVE, "running", None, 0, None),
            ("reused-pid", REUSED, "running", 7, 0, None),
            ("handoff-fresh", DEAD, "claimed", None, 1, now),
            ("handoff-stale", DEAD, "claimed", None, 1, now - grace - 5),
        ):
            conn.execute(
                "UPDATE executions SET process_id='foreign-gateway', pid=?, status=?, process_started_at=?,"
                " handoff_pending=?, handoff_started_at=? WHERE id=?",
                (pid, status, started_at, pending, pending_since, ids[name]),
            )
        # own-process keeps this process id; give it a dead pid to prove the process id wins
        conn.execute("UPDATE executions SET pid=? WHERE id=?", (DEAD, ids["own-process"]))

    abandoned = {row["id"] for row in executions.list_executions(limit=500) if executions.execution_owner_abandoned(row)}
    assert executions.recover_interrupted_executions() == len(abandoned)
    rewritten = {row["id"] for row in executions.list_executions(limit=500) if row["status"] == "unknown"}

    assert abandoned == rewritten == {ids["dead-claimed"], ids["dead-running"], ids["reused-pid"], ids["handoff-stale"]}


def test_owner_abandoned_is_pure(monkeypatch, tmp_path):
    db = _ledger(monkeypatch, tmp_path)
    row = {"status": "running", "process_id": "someone-else", "pid": DEAD, "process_started_at": None,
           "handoff_pending": 0, "handoff_started_at": None}

    assert executions.execution_owner_abandoned(row) is True
    assert executions.execution_owner_abandoned({**row, "status": "completed"}) is False
    assert not db.exists() and not db.parent.exists()
