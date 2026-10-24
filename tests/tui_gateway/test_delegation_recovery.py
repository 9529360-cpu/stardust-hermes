"""delegation.recovery.list: crash receipts proven by durable identity, never UI labels or client claims.

Rows come from the real ``delegate_task(background=True)`` dispatch in an owner process that then dies, and
from the production ``recover_abandoned_delegations`` classification; SQL only mutates age/corruption/ownership."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
DURABLE = "durable-1"
_PRODUCER = r'''
import os, sys, time
from unittest.mock import MagicMock
import tools.delegate_tool as dt
parent = MagicMock(); parent._delegate_depth = 0; parent.session_id = sys.argv[1]; parent._interrupt_requested = False
parent._active_children = []; parent._active_children_lock = None
def child(task_index, goal, child=None, parent_agent=None, **kw):
    if task_index == 1:
        time.sleep(600)
    return {"task_index": task_index, "status": "completed", "summary": f"done: {goal[:20]}", "api_calls": 1,
            "duration_seconds": 0.1, "model": "m", "exit_reason": "completed"}
def build(**kw):
    c = MagicMock(); c._delegate_role = "leaf"; c._subagent_id = f"s{kw['task_index']}"; return c
creds = {"model": "m", "provider": None, "base_url": None, "api_key": None, "api_mode": None, "command": None, "args": None}
dt._build_child_agent = build; dt._run_single_child = child; dt._resolve_delegation_credentials = lambda *a, **k: creds
dt.delegate_task(tasks=[{"goal": "fast member " + "x" * 400, "group": "g"},
                        {"goal": "slow member " + "y" * 400, "group": "g"}], background=True, parent_agent=parent)
time.sleep(2.0)
sys.stdout.flush(); os._exit(1)
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def rpc(home, monkeypatch):
    from tools import async_delegation
    from tui_gateway import server

    frames = []
    response_ready = threading.Event()
    transport = SimpleNamespace(write=lambda frame: (frames.append(frame), response_ready.set(), True)[-1])
    owner = {"session_key": DURABLE, "profile_home": str(home), "history": [], "transport": transport,
             "agent": SimpleNamespace(session_id=DURABLE)}
    monkeypatch.setattr(server, "_get_db", lambda: SimpleNamespace(
        get_compression_lineage=lambda sid: ["compressed-old", sid]))
    monkeypatch.setattr(server, "_sessions", {"runtime-new": owner})
    monkeypatch.setattr(async_delegation, "_records", {})

    def call(method="delegation.recovery.list", *, via=transport, session_id="runtime-new", **params):
        response_ready.clear()
        response = server.dispatch({"id": 1, "method": method,
                                    "params": {"session_id": session_id, **params}}, transport=via)
        if response is not None:
            return response
        assert response_ready.wait(10), f"{method} response timed out"
        frame = frames[-1]
        return frame if "result" in frame or "error" in frame else frame.get("result")

    return server, owner, transport, call


def _owner_dispatches_then_dies(home, session_key=DURABLE):
    """Real dispatch in a separate owner process under a previous runtime session id, killed mid-run."""
    env = {**os.environ, "STARDUST_HOME": str(home), "PYTHONPATH": str(REPO), "STARDUST_UI_SESSION_ID": "runtime-old"}
    proc = subprocess.run([sys.executable, "-c", _PRODUCER, session_key], cwd=REPO, env=env,
                          text=True, capture_output=True, timeout=45)
    assert proc.returncode == 1, proc.stderr[-2000:]


def _rows(sql, args=()):
    from tools import async_delegation as ad
    with ad._DB_LOCK, ad._transaction() as conn:
        return conn.execute(sql, args).fetchall()


def _exec(sql, args=()):
    from tools import async_delegation as ad
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute(sql, args)


def test_exited_owner_becomes_sanitized_unknown_receipt_for_reattached_session(rpc, home):
    from tools import async_delegation as ad

    server, _owner, _transport, call = rpc
    _owner_dispatches_then_dies(home, session_key="compressed-old")
    ((delegation_id, stored_ui, state),) = _rows(
        "SELECT delegation_id, origin_ui_session_id, state FROM async_delegations")
    assert (stored_ui, state) == ("runtime-old", "running")
    receipts = call()["result"]["receipts"]
    assert len(receipts) == 1
    assert _rows("SELECT state FROM async_delegations")[0][0] == "unknown"
    receipt = receipts[0]
    assert set(receipt) == {"delegation_id", "state", "dispatched_at", "completed_at", "delivery_state",
                            "goal", "is_batch", "goal_count"}
    assert (receipt["delegation_id"], receipt["state"]) == (delegation_id, "unknown")
    assert receipt["is_batch"] is True and receipt["goal_count"] == 2
    assert "fast member" in receipt["goal"] and len(receipt["goal"]) <= 200

    # The finished child's result stays in the internal ledger; the receipt never claims it.
    assert "done: fast member" in _rows("SELECT result_json FROM async_delegations")[0][0]
    wire = json.dumps(call())
    for private in ("done: fast", DURABLE, "runtime-old", str(home), "owner_home", "owner_pid", "origin_session"):
        assert private not in wire
    # Async units still have no generation authority: the live roster keeps its empty-array boundary.
    assert call("subagent.list")["result"]["delegations"] == []


def test_other_session_profile_transport_and_client_claims_see_nothing(rpc, home, tmp_path):
    from tools import async_delegation as ad

    server, owner, transport, call = rpc
    _owner_dispatches_then_dies(home)
    assert ad.recover_abandoned_delegations() == 1
    assert len(call()["result"]["receipts"]) == 1

    other_profile = tmp_path / "other-profile"
    server._sessions["runtime-other-session"] = {**owner, "session_key": "durable-2"}
    server._sessions["runtime-other-profile"] = {**owner, "profile_home": str(other_profile)}
    assert call(session_id="runtime-other-session")["result"]["receipts"] == []
    assert call(session_id="runtime-other-profile")["result"]["receipts"] == []
    # A row in THIS profile's db that is recorded as another profile's is not ours either.
    ((task_json,),) = _rows("SELECT task_json FROM async_delegations")
    _exec("UPDATE async_delegations SET task_json=?", (json.dumps({**json.loads(task_json), "owner_home": "elsewhere"}),))
    assert call()["result"]["receipts"] == []
    _exec("UPDATE async_delegations SET task_json=?", (task_json,))
    assert len(call()["result"]["receipts"]) == 1

    unauthorized_frames = []
    unauthorized = SimpleNamespace(write=unauthorized_frames.append)
    assert server.dispatch({"id": 1, "method": "delegation.recovery.list",
                           "params": {"session_id": "runtime-new"}}, transport=unauthorized) is None
    assert unauthorized_frames == []  # never reflect an error to an unbound transport
    for bad in (dict(session_id="missing"), dict(session_id="")):
        assert call(**bad)["error"]["code"] == 4001
    # Identity is never read from params: unknown keys are rejected, and the declared routing hint is ignored.
    for claim in ({"session_key": DURABLE}, {"owner_home": str(home)}, {"profile_home": str(home)}):
        assert call(session_id="runtime-other-session", **claim)["error"]["code"] == 4000
    assert call(session_id="runtime-other-session", profile="default")["result"]["receipts"] == []


def test_running_owner_unknown_outcome_is_not_a_crash_receipt(rpc, home, monkeypatch):
    from tools import async_delegation as ad

    release, finished = threading.Event(), threading.Event()

    def run():
        try:
            assert release.wait(30)
            return {"results": []}
        finally:
            finished.set()

    dispatch = ad.dispatch_async_delegation_batch(
        goals=["live work"], context="private", toolsets=None, role="leaf", model="test",
        session_key=DURABLE, parent_session_id=DURABLE, origin_ui_session_id="runtime-old", runner=run)
    try:
        _server, _owner, _transport, call = rpc
        _exec("UPDATE async_delegations SET state='unknown' WHERE delegation_id=?", (dispatch["delegation_id"],))
        assert call()["result"]["receipts"] == []  # this process still owns it
        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)
        assert [r["delegation_id"] for r in call()["result"]["receipts"]] == [dispatch["delegation_id"]]
    finally:
        release.set()
        assert finished.wait(10)


def test_age_limit_corrupt_rows_and_goal_are_bounded(rpc, home):
    from tools import async_delegation as ad

    _server, _owner, _transport, call = rpc
    _owner_dispatches_then_dies(home)
    assert ad.recover_abandoned_delegations() == 1
    ((task_json, dispatched_at),) = _rows("SELECT task_json, dispatched_at FROM async_delegations")
    for i in range(24):
        _exec("""INSERT INTO async_delegations (delegation_id, origin_session, origin_ui_session_id,
                   parent_session_id, state, dispatched_at, completed_at, updated_at, event_json, result_json,
                   delivery_state, delivery_attempts, owner_pid, owner_started_at, task_json, origin_session_id)
                 SELECT 'clone-' || ?, origin_session, origin_ui_session_id, parent_session_id, state, ?,
                   completed_at, updated_at, event_json, result_json, delivery_state, delivery_attempts,
                   owner_pid, owner_started_at, task_json, origin_session_id
                 FROM async_delegations WHERE delegation_id NOT LIKE 'clone-%' LIMIT 1""", (i, dispatched_at - i - 1))
    ids = [r["delegation_id"] for r in call()["result"]["receipts"]]
    assert len(ids) == 20 and not ids[0].startswith("clone-")  # capped, newest first

    _exec("UPDATE async_delegations SET task_json='{not json' WHERE delegation_id LIKE 'clone-%'")
    assert [r["delegation_id"] for r in call()["result"]["receipts"]] == [ids[0]]  # corrupt payloads fail closed
    _exec("UPDATE async_delegations SET owner_pid='abc' WHERE delegation_id NOT LIKE 'clone-%'")
    assert call()["result"]["receipts"] == []  # unreadable owner fields fail closed

    _exec("UPDATE async_delegations SET owner_pid=NULL, dispatched_at=?", (time.time() - 8 * 86400,))
    assert call()["result"]["receipts"] == []  # outside the retention window
    long_goal = json.dumps({**json.loads(task_json), "goal": "line one\n" + "z" * 5000})
    _exec("UPDATE async_delegations SET dispatched_at=?, task_json=?", (time.time() - 5, long_goal))
    receipts = call()["result"]["receipts"]
    assert len(receipts) == 20 and all(len(r["goal"]) <= 200 and "\n" not in r["goal"] for r in receipts)
