"""RPC recovery uses real profile DB compression ancestry, excluding explicit forks."""
import json
import threading
import time
from types import SimpleNamespace

from hermes_state import SessionDB


def test_recovery_rpc_reads_real_compression_chain_without_branch_leak(tmp_path, monkeypatch):
    from hermes_constants import hermes_home_key
    from tools import async_delegation as ad
    from tui_gateway import server

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("before", source="webui")
        db.end_session("before", "compression")
        db.create_session("after", source="webui", parent_session_id="before")
        db.create_session("fork", source="webui", parent_session_id="before",
                          model_config={"_branched_from": "before"})
        assert db.get_compression_lineage("after") == ["before", "after"]
        assert db.get_compression_lineage("fork") == ["fork"]
        with ad._DB_LOCK, ad._transaction() as conn:
            conn.execute("DELETE FROM async_delegations")
            for sid in ("before", "after", "fork"):
                conn.execute("""INSERT INTO async_delegations
                    (delegation_id,origin_session,parent_session_id,state,dispatched_at,updated_at,
                     owner_pid,owner_started_at,task_json)
                    VALUES(?,?,?,'running',?,?,?,?,?)""",
                    ("deleg-" + sid, sid, sid, time.time(), time.time(), 999999999, 1,
                     json.dumps({"goal": sid, "owner_home": hermes_home_key()})))
        frames = []
        ready = threading.Event()
        transport = SimpleNamespace(write=lambda frame: (frames.append(frame), ready.set(), True)[-1])
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_sessions", {"ui": {
            "session_key": "after", "profile_home": str(tmp_path), "transport": transport}})
        def request():
            ready.clear()
            response = server.dispatch({"id": 1, "method": "delegation.recovery.list",
                                        "params": {"session_id": "ui"}}, transport=transport)
            if response is not None:
                return response
            assert ready.wait(5)
            return frames[-1]
        assert {r["goal"] for r in request()["result"]["receipts"]} == {"before", "after"}
        server._sessions["ui"]["session_key"] = "fork"
        assert {r["goal"] for r in request()["result"]["receipts"]} == {"fork"}
    finally:
        db.close()
