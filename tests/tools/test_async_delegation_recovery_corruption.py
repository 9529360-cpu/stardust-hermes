"""Corrupt durable rows are isolated from crash recovery and snapshot reads."""
import json
import time

import pytest


def _insert(conn, did, *, pid="999999999", task="{}", result="{}"):
    conn.execute("""INSERT INTO async_delegations(delegation_id, origin_session, origin_ui_session_id,
                   parent_session_id, dispatched_at, updated_at, owner_pid, owner_started_at,
                   task_json, result_json, state)
                   VALUES(?,?,?,?,?,?,?,?,?,?,'running')""",
                 (did, "session", "runtime", "session", time.time(), time.time(), pid, 1, task, result))


def test_recovery_skips_corrupt_pid_and_task_json(tmp_path, monkeypatch):
    from hermes_constants import hermes_home_key
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    home = hermes_home_key()
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute("DELETE FROM async_delegations")
        _insert(conn, "bad-pid", pid="not-a-pid")
        _insert(conn, "bad-task", task="{malformed")
        _insert(conn, "good", task=json.dumps({"goal": "recover me", "owner_home": home}))

    assert ad.recover_abandoned_delegations() == 2
    states = dict(ad._connect().execute("SELECT delegation_id,state FROM async_delegations").fetchall())
    assert states == {"bad-pid": "running", "bad-task": "unknown", "good": "unknown"}


def test_durable_snapshot_skips_malformed_json_rows(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute("DELETE FROM async_delegations")
        _insert(conn, "bad-json", task="{bad", result="{bad")
        _insert(conn, "good", task=json.dumps({"goal": "valid snapshot"}), result=json.dumps({"status": "completed"}))
        conn.execute("UPDATE async_delegations SET state='completed' WHERE delegation_id='good'")
    snapshots = ad.list_durable_delegations()
    assert [item["delegation_id"] for item in snapshots] == ["good"]
    assert snapshots[0]["goal"] == "valid snapshot"
    assert snapshots[0]["result"]["status"] == "completed"


@pytest.mark.parametrize("task,result", [("{}", "{}"), ("", ""), (None, None)])
def test_snapshot_preserves_legal_empty_payloads(tmp_path, monkeypatch, task, result):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute("DELETE FROM async_delegations")
        _insert(conn, "empty", task=task, result=result)
        conn.execute("UPDATE async_delegations SET state='completed'")
    snapshots = ad.list_durable_delegations()
    assert len(snapshots) == 1
    assert snapshots[0]["delegation_id"] == "empty"
    assert snapshots[0]["result"] == {}


@pytest.mark.parametrize("bad_payload", ["{bad", "[]", "null", "42"])
def test_startup_replay_isolates_invalid_events(tmp_path, monkeypatch, bad_payload):
    from queue import Queue
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute("DELETE FROM async_delegations")
        for did, payload in (("a-bad", bad_payload), ("b-good", json.dumps({"delegation_id": "b-good"}))):
            _insert(conn, did)
            conn.execute("UPDATE async_delegations SET state='completed',event_json=?,completed_at=? WHERE delegation_id=?",
                         (payload, time.time(), did))
    queue = Queue()
    assert ad.restore_undelivered_completions(queue) == 1
    assert queue.get_nowait() == {"delegation_id": "b-good", "restored": True}
    assert queue.empty()
    assert ad._connect().execute("SELECT delivery_state FROM async_delegations WHERE delegation_id='a-bad'").fetchone()[0] == "pending"


def test_session_replay_logs_corrupt_event_and_keeps_scanning(tmp_path, monkeypatch, caplog):
    from queue import Queue
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute("DELETE FROM async_delegations")
        for did, event in (("a-bad", "{bad"), ("b-good", json.dumps({"session_key": "owned", "goal": "healthy"}))):
            _insert(conn, did)
            conn.execute("UPDATE async_delegations SET state='completed',event_json=? WHERE delegation_id=?", (event, did))
    queue = Queue()
    with caplog.at_level("DEBUG", logger="tools.async_delegation"):
        for _ in range(3):
            assert ad.restore_matching_undelivered_completions(queue, lambda event: event.get("session_key") == "owned") == 1
            assert queue.get_nowait()["goal"] == "healthy"
    assert queue.empty()
    assert "a-bad" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= 30]
    assert ad._connect().execute("SELECT delivery_state FROM async_delegations WHERE delegation_id='a-bad'").fetchone()[0] == "pending"
