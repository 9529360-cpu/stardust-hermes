"""Recovery receipts are session-owned, sanitized views of the delegation ledger."""

import json
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from tui_gateway import server
    from tools import async_delegation

    transport = SimpleNamespace(write=lambda frame: True)
    owner = {
        "session_key": "parent",
        "history": [],
        "transport": transport,
        "profile_home": str(tmp_path),
    }
    monkeypatch.setattr(server, "_sessions", {"ui-owner": owner})
    monkeypatch.setattr(async_delegation, "_db_path", lambda: tmp_path / "state.db")

    def call(*, via=transport, session_id="ui-owner"):
        return server.dispatch(
            {
                "id": 1,
                "method": "delegation.recovery.list",
                "params": {"session_id": session_id},
            },
            transport=via,
        )

    return server, owner, transport, call, async_delegation


def _insert(async_delegation, *, delegation_id, origin_session, origin_ui="", state="unknown",
            completed_at=None, task=None):
    now = time.time()
    completed_at = now if completed_at is None else completed_at
    with async_delegation._DB_LOCK, async_delegation._transaction() as conn:
        conn.execute(
            """INSERT INTO async_delegations
               (delegation_id, origin_session, origin_ui_session_id, state,
                dispatched_at, completed_at, updated_at, task_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                delegation_id,
                origin_session,
                origin_ui,
                state,
                now - 30,
                completed_at,
                now,
                json.dumps(task or {}),
            ),
        )


def test_recovery_list_is_owned_recent_and_sanitized(runtime):
    _server, _owner, _transport, call, delegation = runtime
    now = time.time()
    private_task = {
        "goal": "Finish the private report",
        "context": "SECRET HANDOFF",
        "toolsets": ["terminal"],
        "model": "private-model",
        "scope_id": "tenant-secret",
    }
    _insert(delegation, delegation_id="owned-key", origin_session="parent", task=private_task)
    _insert(delegation, delegation_id="owned-ui", origin_session="other", origin_ui="ui-owner",
            task={"goals": ["Check inbox", "Summarize"], "context": "PRIVATE"})
    _insert(delegation, delegation_id="foreign", origin_session="other", origin_ui="other-ui",
            task={"goal": "Foreign secret"})
    _insert(delegation, delegation_id="completed", origin_session="parent", state="completed",
            task={"goal": "Already done"})
    _insert(delegation, delegation_id="old", origin_session="parent", completed_at=now - 2 * 24 * 60 * 60,
            task={"goal": "Old interruption"})

    reply = call()
    rows = reply["result"]["receipts"]
    assert {row["delegation_id"] for row in rows} == {"owned-key", "owned-ui"}
    assert all(row["reason"] == "owner_exited" for row in rows)
    assert next(row for row in rows if row["delegation_id"] == "owned-ui")["task_count"] == 2

    wire = json.dumps(reply)
    assert "SECRET HANDOFF" not in wire
    assert "PRIVATE" not in wire
    assert "private-model" not in wire
    assert "tenant-secret" not in wire
    assert "Foreign secret" not in wire
    assert "origin_session" not in wire
    assert "origin_ui_session_id" not in wire


def test_recovery_list_requires_exact_live_session_transport(runtime):
    server, owner, transport, call, delegation = runtime
    _insert(delegation, delegation_id="owned", origin_session="parent", task={"goal": "Recover me"})

    assert "error" in call(via=SimpleNamespace(write=lambda frame: True))
    assert "error" in call(session_id="missing")
    server._sessions["ui-owner"] = {**owner}
    assert "error" in call(via=transport)
