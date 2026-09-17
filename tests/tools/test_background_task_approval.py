import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools import background_task_approval as approval


@pytest.fixture
def running_task(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    identities = getattr(kb, "_INITIALIZED_FILE_IDENTITIES", None)
    if identities is not None:
        identities.pop(str(db_path.resolve()), None)
    kb.init_db()
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="Background external action", assignee="default")
        assert kb.claim_task(conn, task_id) is not None
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    return task_id


def _request(args, *, reason="Send the message"):
    return approval.authorize_or_block_current_worker(
        "connectors__mail__send_message",
        args,
        reason=reason,
        rule_key="stardust:connector-write:mail:send_message",
    )


def test_worker_request_is_durable_deduplicated_and_redacted(running_task):
    args = {"to": "client@example.test", "message": "hello", "token": "SUPER-SECRET"}
    first = _request(args)
    second = _request(args)

    assert first.allowed is False
    assert first.approval_id.startswith("apr-")
    assert second.approval_id == first.approval_id
    assert first.approval_id in first.message

    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, running_task)
        assert task.status == "blocked"
        assert task.block_kind == "needs_input"
        states = approval.list_approvals(conn, running_task)
        assert len(states) == 1
        assert states[0]["state"] == "pending"
        assert states[0]["tool_name"] == "connectors__mail__send_message"
        raw_payloads = "\n".join(
            str(row["payload"] or "")
            for row in conn.execute(
                "SELECT payload FROM task_events WHERE task_id = ? AND kind LIKE 'assistant_approval_%'",
                (running_task,),
            ).fetchall()
        )
    assert "SUPER-SECRET" not in raw_payloads
    assert "client@example.test" not in raw_payloads
    assert "args_sha256" in raw_payloads


def test_approved_exact_call_is_consumed_once(running_task):
    args = {"to": "client@example.test", "message": "approved"}
    requested = _request(args)

    with kbc.connect_closing() as conn:
        decision = approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=requested.approval_id
        )
        assert decision["decision"] == "approved"
        assert decision["resumed"] is True
        assert kb.claim_task(conn, running_task) is not None

    consumed = _request(args)
    assert consumed.allowed is True
    assert consumed.approval_id == requested.approval_id

    # A grant authorizes one exact execution only. A second identical attempt must ask again.
    second = _request(args)
    assert second.allowed is False
    assert second.approval_id != requested.approval_id

    with kbc.connect_closing() as conn:
        states = approval.list_approvals(conn, running_task)
        by_id = {item["approval_id"]: item for item in states}
        assert by_id[requested.approval_id]["state"] == "consumed"
        assert by_id[second.approval_id]["state"] == "pending"


def test_argument_change_cannot_consume_old_grant(running_task):
    requested = _request({"message": "version one"})
    with kbc.connect_closing() as conn:
        approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=requested.approval_id
        )
        assert kb.claim_task(conn, running_task) is not None

    changed = _request({"message": "version two"})
    assert changed.allowed is False
    assert changed.approval_id != requested.approval_id

    with kbc.connect_closing() as conn:
        states = {item["approval_id"]: item for item in approval.list_approvals(conn, running_task)}
        assert states[requested.approval_id]["state"] == "approved"
        assert states[changed.approval_id]["state"] == "pending"
        assert states[requested.approval_id]["args_sha256"] != states[changed.approval_id]["args_sha256"]


def test_denial_leaves_task_blocked_and_resume_requires_new_request(running_task):
    args = {"message": "do not send yet"}
    requested = _request(args)
    with kbc.connect_closing() as conn:
        denied = approval.decide_task_approval(
            conn,
            kb,
            running_task,
            decision="deny",
            approval_id=requested.approval_id,
            reason="Need to revise the text",
        )
        assert denied["decision"] == "denied"
        assert denied["resumed"] is False
        assert kb.get_task(conn, running_task).status == "blocked"
        assert kb.unblock_task(conn, running_task) is True
        assert kb.claim_task(conn, running_task) is not None

    again = _request(args)
    assert again.allowed is False
    assert again.approval_id != requested.approval_id

    with kbc.connect_closing() as conn:
        states = {item["approval_id"]: item for item in approval.list_approvals(conn, running_task)}
        assert states[requested.approval_id]["state"] == "denied"
        assert states[again.approval_id]["state"] == "pending"
        comments = kb.list_comments(conn, running_task)
        assert any("Need to revise the text" in comment.body for comment in comments)


def test_approval_retry_recovers_when_first_unblock_loses_race(running_task, monkeypatch):
    requested = _request({"message": "retry-safe"})
    original_unblock = kb.unblock_task
    monkeypatch.setattr(kb, "unblock_task", lambda *args, **kwargs: False)
    with kbc.connect_closing() as conn:
        first = approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=requested.approval_id
        )
        assert first["resumed"] is False
        assert first["already_decided"] is False
        assert kb.get_task(conn, running_task).status == "blocked"

    monkeypatch.setattr(kb, "unblock_task", original_unblock)
    with kbc.connect_closing() as conn:
        retry = approval.decide_task_approval(
            conn, kb, running_task, decision="approve", approval_id=requested.approval_id
        )
        assert retry["already_decided"] is True
        assert retry["resumed"] is True
        kinds = [
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM task_events WHERE task_id = ? AND kind = 'assistant_approval_granted'",
                (running_task,),
            ).fetchall()
        ]
    assert kinds == ["assistant_approval_granted"]


def test_approval_fingerprint_is_stable_and_order_independent():
    first = approval.call_fingerprint("Tool_Name", {"b": 2, "a": 1})
    second = approval.call_fingerprint("tool_name", {"a": 1, "b": 2})
    changed = approval.call_fingerprint("tool_name", {"a": 1, "b": 3})
    assert first == second
    assert first != changed
    assert len(first) == 64
