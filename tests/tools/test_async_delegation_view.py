"""Task Center delegation reads stay profile-scoped and never recover state."""

from __future__ import annotations

import json
import sqlite3

from hermes_constants import get_hermes_home
from tools.async_delegation_view import peek_durable_delegations


def _write_ledger(path, *, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE async_delegations (
                delegation_id TEXT PRIMARY KEY,
                parent_session_id TEXT,
                state TEXT NOT NULL,
                dispatched_at REAL NOT NULL,
                completed_at REAL,
                updated_at REAL NOT NULL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                task_json TEXT,
                result_json TEXT,
                event_json TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO async_delegations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_missing_database_is_an_empty_read_without_directory_creation():
    home = get_hermes_home()

    assert peek_durable_delegations() == []
    assert not (home / "state.db").exists()


def test_missing_table_is_an_empty_read_and_database_bytes_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE other (value TEXT)")
    before = path.read_bytes()

    assert peek_durable_delegations() == []
    assert path.read_bytes() == before


def test_dead_owner_is_classified_in_memory_without_rewriting_row(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    token = set_hermes_home_override(tmp_path)
    try:
        path = tmp_path / "state.db"
        _write_ledger(
            path,
            rows=[
                (
                    "dead", "session-1", "running", 100.0, None, 110.0, 910001, None,
                    json.dumps({"goal": "Recover the task", "owner_home": str(tmp_path)}),
                    "{}", "{}",
                ),
            ],
        )
        before = path.read_bytes()
        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)

        rows = peek_durable_delegations()

        assert rows[0]["status"] == "unknown"
        assert rows[0]["parent_session_id"] == "session-1"
        assert path.read_bytes() == before
        check = sqlite3.connect(path)
        try:
            assert check.execute("SELECT state FROM async_delegations").fetchone()[0] == "running"
        finally:
            check.close()
    finally:
        reset_hermes_home_override(token)


def test_live_and_reused_process_identity_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    _write_ledger(
        path,
        rows=[
            ("live", "s1", "running", 100.0, None, 110.0, 910002, 5, "{}", "{}", "{}"),
            ("reused", "s2", "running", 100.0, None, 110.0, 910003, 7, "{}", "{}", "{}"),
        ],
    )
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: True)
    monkeypatch.setattr("gateway.status.get_process_start_time", lambda pid: 5)

    states = {row["delegation_id"]: row["status"] for row in peek_durable_delegations()}

    assert states == {"live": "running", "reused": "unknown"}
