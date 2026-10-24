"""session.create with seeded messages: the seed is durable before the first prompt, and durable once."""

import pytest

from hermes_state import SessionDB
from tui_gateway import server


def _quiet_create(monkeypatch, db):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)


def _create(params: dict) -> dict:
    resp = server.handle_request({"id": "create", "method": "session.create", "params": params})
    assert "result" in resp, resp
    return resp["result"]


def test_parentless_seed_survives_a_restart_and_hides_its_runbook(monkeypatch, tmp_path):
    """A client that opens a chat with its first turns already written (no parent) gets a durable row and
    transcript at create: a restart before the first prompt resumes it, the hidden runbook stays out of the
    transcript on the wire, and the seed is not written a second time by the first-submit path."""
    db = SessionDB(db_path=tmp_path / "state.db")
    _quiet_create(monkeypatch, db)
    sids = []
    try:
        result = _create({
            "cols": 96, "source": "desktop", "title": "Welcome to Hermes",
            "messages": [
                {"role": "user", "content": "Private setup runbook", "display_kind": "hidden"},
                {"role": "assistant", "content": "Welcome to Hermes"},
                # Only "hidden" is accepted from the wire; other kinds are stamped by the gateway itself.
                {"role": "user", "content": "Second question", "display_kind": "steer"},
            ]})
        sids.append(result["session_id"])
        key = result["stored_session_id"]
        assert result["info"]["durable"] is True
        assert "persistence_error" not in result["info"]
        assert [m["role"] for m in result["messages"]] == ["assistant", "user"]
        assert result["message_count"] == 2  # counts what is on the wire, as session.resume does
        live = server.handle_request({"id": "live", "method": "session.resume", "params": {"session_id": key, "cols": 96}})["result"]
        assert (live["message_count"], len(live["messages"])) == (2, 2)  # the reuse-live path counts the wire too

        assert db.get_session(key)["title"] == "Welcome to Hermes"
        rows = db.get_messages_as_conversation(key)
        assert [r["content"] for r in rows] == ["Private setup runbook", "Welcome to Hermes", "Second question"]
        assert rows[0]["display_kind"] == "hidden"
        assert rows[2].get("display_kind") is None
        listed = server.handle_request({"id": "list", "method": "session.list", "params": {}})["result"]["sessions"]
        assert next(s for s in listed if s["id"] == key)["preview"].startswith("Second question")  # the hidden row is not the preview

        server._sessions.pop(sids.pop())  # the gateway restarts; only state.db remains
        resumed = server.handle_request({"id": "resume", "method": "session.resume", "params": {"session_id": key, "cols": 96}})
        assert "result" in resumed, resumed
        sids.append(resumed["result"]["session_id"])
        assert [m["role"] for m in resumed["result"]["messages"]] == ["assistant", "user"]

        assert server._persist_session_row_for_submit("rid", server._sessions[sids[-1]]) is None  # the first prompt.submit
        assert len(db.get_messages_as_conversation(key)) == 3
        assert [hit["session_id"] for hit in db.search_messages("Second question")] == [key]
        assert db.search_messages("Private setup runbook") == []  # the hidden row is not searchable either
    finally:
        for sid in sids:
            server._sessions.pop(sid, None)
        db.close()


def test_branch_child_seed_is_written_once(monkeypatch, tmp_path):
    """A seeded branch child persists its copied transcript at create (#93959); the first prompt's seed
    persist is the fallback for a failed create-time copy, not a second copy."""
    db = SessionDB(db_path=tmp_path / "state.db")
    _quiet_create(monkeypatch, db)
    seed = [{"role": "user", "content": "hello from parent"}, {"role": "assistant", "content": "parent reply"}]
    db.create_session("parent-1", source="desktop")
    db.append_messages_batch("parent-1", seed)
    db.set_session_title("parent-1", "Parent chat")
    sid = None
    try:
        result = _create({"cols": 96, "source": "desktop", "parent_session_id": "parent-1", "messages": seed})
        sid, key = result["session_id"], result["stored_session_id"]
        assert [r["content"] for r in db.get_messages_as_conversation(key)] == ["hello from parent", "parent reply"]

        assert server._persist_session_row_for_submit("rid", server._sessions[sid]) is None  # the first prompt.submit
        assert [r["content"] for r in db.get_messages_as_conversation(key)] == ["hello from parent", "parent reply"]
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


def test_partial_seed_copy_is_rolled_back_not_duplicated(monkeypatch, tmp_path):
    """A seed copy that fails after the row exists leaves no row behind: the first prompt's retry copies the
    whole seed again, so a kept partial copy would double it."""
    db = SessionDB(db_path=tmp_path / "state.db")
    _quiet_create(monkeypatch, db)
    real_append, calls = db.append_messages_batch, []

    def flaky_append(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("copy failed after the row was committed")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(db, "append_messages_batch", flaky_append)
    sid = None
    try:
        result = _create({"cols": 96, "source": "desktop", "title": "Welcome",
                          "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
        sid = result["session_id"]
        key = server._sessions[sid]["session_key"]
        assert result["stored_session_id"] == ""
        assert result["info"]["durable"] is False
        assert result["info"]["persistence_error"]
        assert db.get_session(key) is None  # rolled back, so the first prompt starts clean

        assert server._persist_session_row_for_submit("rid", server._sessions[sid]) is None
        assert [r["content"] for r in db.get_messages_as_conversation(key)] == ["hi", "hello"]
        assert server._sessions[sid]["pending_title"] == "Welcome"  # still queued: the turn applies it, as for any lazy row
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


@pytest.mark.parametrize("branch", [False, True])
@pytest.mark.parametrize("failure", ["messages", "title", "unavailable"])
def test_seed_failure_receipt_keeps_retryable_history(monkeypatch, tmp_path, branch, failure):
    db = SessionDB(db_path=tmp_path / "state.db")
    _quiet_create(monkeypatch, db)
    seed = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "reply"}]
    if branch:
        db.create_session("parent", source="desktop")
        db.set_session_title("parent", "Parent")
    if failure == "unavailable":
        monkeypatch.setattr(server, "_get_db", lambda: None)
    elif failure == "messages":
        db._conn.execute("CREATE TRIGGER reject_seed BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'reject seed'); END")
    else:
        db._conn.execute("CREATE TRIGGER reject_title BEFORE UPDATE OF title ON sessions BEGIN SELECT RAISE(ABORT, 'reject title'); END")
    sid = None
    try:
        result = _create({"messages": seed, "title": "Imported", **({"parent_session_id": "parent"} if branch else {})})
        sid = result["session_id"]
        record = server._sessions[sid]
        assert result["info"]["durable"] is False
        assert result["info"]["persistence_error"]
        assert result["stored_session_id"] == ""
        assert result["message_count"] == len(result["messages"]) == 2
        assert len(record["history"]) == 2
        assert not record.get("_branch_seed_persisted")
        assert db.get_session(record["session_key"]) is None
        if failure == "unavailable":
            monkeypatch.setattr(server, "_get_db", lambda: db)
        else:
            db._conn.execute("DROP TRIGGER " + ("reject_seed" if failure == "messages" else "reject_title"))
        assert server._persist_session_row_for_submit("retry", record) is None
        assert [m["content"] for m in db.get_messages_as_conversation(record["session_key"])] == ["hello", "reply"]
    finally:
        if sid:
            server._sessions.pop(sid, None)
        db.close()


def test_empty_draft_retains_lazy_receipt(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    _quiet_create(monkeypatch, db)
    result = _create({})
    try:
        assert result["stored_session_id"]
        assert result["message_count"] == 0
        assert "durable" not in result["info"]
        assert db.get_session(result["stored_session_id"]) is None
    finally:
        server._sessions.pop(result["session_id"], None)
        db.close()
