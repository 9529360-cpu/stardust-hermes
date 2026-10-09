"""Unified ledger behavior without model calls or real child processes."""
from types import SimpleNamespace

import pytest

from tools import async_delegation as async_work
from tools import work_ledger as ledger
from hermes_constants import hermes_home_key


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    async_work._reset_for_tests()
    monkeypatch.setattr(ledger, "process_registry", SimpleNamespace(list_sessions=lambda **kw: []))
    monkeypatch.setattr(ledger, "list_active_subagents", lambda: [])
    yield
    async_work._reset_for_tests()


def persist(rid, status="running"):
    record = {"delegation_id": rid, "goal": "Research", "dispatched_at": 100.0,
              "status": status, "owner_home": hermes_home_key()}
    async_work._persist_dispatch(record)
    if status != "running":
        async_work._persist_completion({**record, "completed_at": 110.0},
                                       {"summary": "Done", "status": status})
    return record


def test_restart_retains_history_and_recovers_abandoned(monkeypatch):
    persist("done", "completed")
    persist("lost")
    with async_work._DB_LOCK, async_work._transaction() as conn:
        conn.execute("UPDATE async_delegations SET owner_pid=NULL WHERE delegation_id='lost'")
    async_work._reset_for_tests()  # fresh runtime, same durable home
    work = {r["id"]: r for r in ledger.list_work()}
    assert work["delegation:done"]["status"] == "completed"
    assert work["delegation:done"]["detail"]["summary"] == "Done"
    assert work["delegation:lost"]["status"] == "interrupted"
    assert ledger.cancel_work("delegation:done")["status"] == "already_finished"
    assert ledger.cancel_work("missing")["status"] == "not_found"


def test_normalization_and_targeted_cancel(monkeypatch):
    interrupted = []
    live = persist("live")
    live["interrupt_fn"] = lambda: interrupted.append("live")
    with async_work._records_lock:
        async_work._records["live"] = live
    killed = []
    monkeypatch.setattr(ledger, "process_registry", SimpleNamespace(
        list_sessions=lambda **kw: [
            {"session_id": "live", "command": "sleep", "status": "running", "started_at": 101.0},
            {"session_id": "bad", "command": "false", "status": "exited", "exit_code": 1},
            {"session_id": "killed", "status": "exited", "completion_reason": "killed"}],
        kill_process=lambda id, **kw: killed.append((id, kw)) or {"status": "killed"}))
    monkeypatch.setattr(ledger, "list_active_subagents", lambda: [
        {"subagent_id": "child", "goal": "Think", "started_at": 102.0, "status": "running"}])
    records = {r["id"]: r for r in ledger.list_work()}
    assert len(records) == 5  # live durable/memory delegation is deduplicated
    assert records["process:bad"]["status"] == "failed"
    assert records["process:killed"]["status"] == "cancelled"
    assert records["subagent:child"]["started_at"] == 102.0
    assert set(records["delegation:live"]) == {"id", "kind", "title", "status", "started_at", "updated_at", "detail"}
    assert ledger.cancel_work("delegation:live")["status"] == "interrupt_requested"
    assert interrupted == ["live"]
    assert ledger.cancel_work("process:live")["status"] == "cancelled"
    assert killed == [("live", {"source": "work.cancel", "consume_output": False})]
    assert ledger.cancel_work("process:bad")["status"] == "already_finished"


def test_foreign_home_and_uncontrollable_live_record(monkeypatch):
    live = persist("external")
    assert ledger.cancel_work("delegation:external")["status"] == "unavailable"
    with async_work._records_lock:
        async_work._records["foreign"] = {**live, "delegation_id": "foreign", "owner_home": "other"}
    assert "delegation:foreign" not in {r["id"] for r in ledger.list_work()}


def test_subagent_control_is_cooperative(monkeypatch):
    monkeypatch.setattr(ledger, "list_active_subagents", lambda: [
        {"subagent_id": "child", "goal": "Think", "status": "running"}])
    calls = []
    monkeypatch.setattr(ledger, "interrupt_subagent", lambda id: calls.append(id) or True)
    assert ledger.cancel_work("subagent:child")["status"] == "interrupt_requested"
    assert calls == ["child"]
    assert ledger.cancel_work("subagent:child", include_subagents=False)["status"] == "not_found"
    assert ledger.list_work()[0]["status"] == "running"
