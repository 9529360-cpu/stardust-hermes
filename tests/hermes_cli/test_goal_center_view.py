"""Persisted goal views use read-only SQLite access and never initialize SessionDB."""

from __future__ import annotations

import json
import sqlite3

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli.goals_view import list_goal_states


def test_missing_database_is_an_empty_view_without_creating_a_profile_home(tmp_path):
    token = set_hermes_home_override(tmp_path)
    try:
        assert list_goal_states() == []
        assert not (tmp_path / "state.db").exists()
    finally:
        reset_hermes_home_override(token)


def test_reads_goal_rows_without_mutating_database_or_returning_cleared_goals(tmp_path):
    token = set_hermes_home_override(tmp_path)
    try:
        path = tmp_path / "state.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO state_meta(key, value) VALUES (?, ?)",
                [
                    (
                        "goal:s-1",
                        json.dumps({
                            "goal": "Ship the task center",
                            "status": "active",
                            "turns_used": 2,
                            "max_turns": 20,
                            "created_at": 100.0,
                            "last_turn_at": 200.0,
                        }),
                    ),
                    ("goal:s-2", json.dumps({"goal": "Cleared", "status": "cleared"})),
                    ("not-a-goal:s-3", json.dumps({"goal": "Wrong prefix", "status": "active"})),
                    ("goal:bad", "not-json"),
                ],
            )
        before = path.read_bytes()

        rows = list_goal_states()

        assert len(rows) == 1
        assert rows[0]["session_id"] == "s-1"
        assert rows[0]["goal"]["goal"] == "Ship the task center"
        assert rows[0]["waiting"] is False
        assert path.read_bytes() == before
    finally:
        reset_hermes_home_override(token)


def test_timed_wait_barrier_is_projected_without_touching_goal_row(tmp_path):
    token = set_hermes_home_override(tmp_path)
    try:
        path = tmp_path / "state.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO state_meta VALUES (?, ?)",
                (
                    "goal:s-1",
                    json.dumps({
                        "goal": "Wait for the deployment",
                        "status": "active",
                        "last_verdict": "wait",
                        "waiting_until": 4_102_444_799,
                        "waiting_since": 100.0,
                        "waiting_on_delegations": 0,
                    }),
                ),
            )
        before = path.read_bytes()

        rows = list_goal_states()

        assert rows[0]["waiting"] is True
        assert path.read_bytes() == before
    finally:
        reset_hermes_home_override(token)
