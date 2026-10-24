"""Final replies retain their text while reporting the actual transcript commit."""
import logging
import sqlite3
from unittest.mock import patch

from tests.agent.test_text_turn_incremental_persistence import loop_agent  # noqa: F401


def _run(agent, answer="visible reply"):
    from tests.agent.test_run_agent import _mock_response
    agent.client.chat.completions.create.side_effect = [
        _mock_response(content=answer, finish_reason="stop")
    ]
    return agent.run_conversation("hello")


def test_failed_final_commit_is_explicit_and_retry_writes_once(loop_agent, tmp_path, caplog):
    from hermes_state import SessionDB
    agent = loop_agent
    db_path = tmp_path / "outcome.db"
    db = SessionDB(db_path)
    agent._session_db = db
    agent._session_db_created = False
    original = agent.client.chat.completions.create
    from tests.agent.test_run_agent import _mock_response

    def reject_final(**kwargs):
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TRIGGER reject_final BEFORE INSERT ON messages "
                         "WHEN new.role='assistant' BEGIN SELECT RAISE(ABORT, 'final rejected'); END")
        return _mock_response(content="visible reply", finish_reason="stop")

    original.side_effect = reject_final
    try:
        with caplog.at_level(logging.WARNING):
            result = agent.run_conversation("hello")
        assert result["final_response"] == "visible reply"
        assert result["completed"] is False
        assert result["failed"] is True
        assert result["persistence_status"] == "failed"
        assert result["persistence_error_cause"] == "sqlite_constraint_trigger"
        assert result["failure_reason"] == "session_persistence_failed:sqlite_constraint_trigger"
        assert any("final transcript" in r.message and "sqlite_constraint_trigger" in r.message
                   for r in caplog.records)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT role FROM messages ORDER BY id").fetchall() == [("user",)]
            conn.execute("DROP TRIGGER reject_final")
        assert agent._persist_session(result["messages"], []) is True
        assert agent._persist_session(result["messages"], []) is True
        assert agent._last_persistence_error_cause is None
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT role FROM messages ORDER BY id").fetchall() == [("user",), ("assistant",)]
        recovered = _run(agent, "next reply")
        assert recovered["completed"] is True
        assert recovered["failed"] is False
        assert recovered["persistence_status"] == "saved"
    finally:
        db.close()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT role, content FROM messages WHERE role='assistant' ORDER BY id").fetchall() == [
            ("assistant", "visible reply"), ("assistant", "next reply")]


def test_finalizer_retry_can_recover_incremental_failure(loop_agent, tmp_path):
    from hermes_state import SessionDB
    agent = loop_agent
    agent._session_db = SessionDB(tmp_path / "retry.db")
    agent._session_db_created = False
    real_flush = agent._flush_messages_to_session_db
    rejected = False

    def transient(messages, history=None):
        nonlocal rejected
        if messages[-1].get("role") == "assistant" and not rejected:
            rejected = True
            return False
        return real_flush(messages, history)

    try:
        with patch.object(agent, "_flush_messages_to_session_db", side_effect=transient):
            result = _run(agent)
        assert rejected
        assert result["completed"] is True
        assert result["failed"] is False
        assert result["persistence_status"] == "saved"
    finally:
        agent._session_db.close()


def test_persist_session_does_not_acknowledge_failed_flush(loop_agent):
    agent = loop_agent
    with patch.object(agent, "_flush_messages_to_session_db", return_value=False), patch(
        "agent.agent_runtime_helpers.note_turn_persisted"
    ) as note:
        assert agent._persist_session([{"role": "assistant", "content": "reply"}], []) is False
    note.assert_not_called()
