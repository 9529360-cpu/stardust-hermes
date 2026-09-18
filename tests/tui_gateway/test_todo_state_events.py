"""Todo snapshots bypass optional tool-progress display settings."""

import json
import threading
import types

import tui_gateway.server as server


def test_todo_completion_always_emits_snapshot_and_compat_event(monkeypatch):
    sid = "todo-state-test"
    events = []
    session = {
        "agent": None,
        "edit_snapshots": {},
        "tool_started_at": {},
        "tool_progress_mode": "off",
    }
    monkeypatch.setitem(server._sessions, sid, session)
    monkeypatch.setattr(server, "_tool_progress_enabled", lambda _sid: False)
    monkeypatch.setattr(server, "_tool_lifecycle_required_for_ui", lambda _name: False)
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, event_sid, payload=None: events.append(
            (event, event_sid, payload)
        ),
    )

    state = {
        "todos": [{"id": "1", "content": "Work", "status": "in_progress"}],
        "revision": 9,
    }
    server._on_tool_complete(sid, "call-1", "todo_list", {}, json.dumps(state))

    assert [event[0] for event in events] == ["tool.complete", "todo.updated"]
    assert events[-1] == ("todo.updated", sid, state)
    assert session["todo_state"] == state


def test_non_todo_completion_stays_suppressed_when_progress_is_off(monkeypatch):
    sid = "ordinary-tool-test"
    events = []
    monkeypatch.setitem(
        server._sessions,
        sid,
        {
            "agent": None,
            "edit_snapshots": {},
            "tool_started_at": {},
            "tool_progress_mode": "off",
        },
    )
    monkeypatch.setattr(server, "_tool_progress_enabled", lambda _sid: False)
    monkeypatch.setattr(server, "_tool_lifecycle_required_for_ui", lambda _name: False)
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))

    server._on_tool_complete(sid, "call-1", "terminal", {}, "ok")

    assert events == []


def test_live_snapshot_prefers_the_highest_revision():
    class Store:
        @staticmethod
        def snapshot():
            return {"todos": [], "revision": 4}

    class Agent:
        _todo_store = Store()

    session = {
        "agent": Agent(),
        "todo_state": {
            "todos": [{"id": "1", "content": "Current", "status": "pending"}],
            "revision": 5,
        },
    }

    payload = server._attach_todo_state({}, session)

    assert payload["todo_state"]["revision"] == 5


def test_unused_store_is_not_attached():
    class Store:
        @staticmethod
        def snapshot():
            return {"todos": [], "revision": 0}

    class Agent:
        _todo_store = Store()

    payload = server._attach_todo_state({}, {"agent": Agent()})

    assert "todo_state" not in payload


def test_empty_list_at_nonzero_revision_is_a_real_clear():
    state = server._normalize_todo_state({"todos": [], "revision": 2})

    assert state == {"todos": [], "revision": 2}


def _todo_history(state: dict) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "todo-call",
                "type": "function",
                "function": {"name": "todo_list", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "todo-call", "content": json.dumps(state)},
    ]


def test_resume_record_uses_persisted_todo_without_live_agent_or_tool_history(tmp_path):
    from hermes_state import SessionDB
    from tools.todo_tool import persist_todo_session_state, TodoStore

    store = TodoStore()
    store.restore(
        [{"id": "1", "content": "Resume immediately", "status": "in_progress"}],
        revision=7,
    )
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("persisted-resume", source="tui")
        assert persist_todo_session_state(db, "persisted-resume", store)
        ctx = server._Resume("rid", {}, "persisted-resume")
        ctx.db = db

        record = ctx.record("tui", str(tmp_path), [])

    assert record["todo_state"] == store.snapshot()


def test_resume_record_prefers_newer_history_todo_revision(tmp_path):
    from hermes_state import SessionDB
    from tools.todo_tool import persist_todo_session_state, TodoStore

    store = TodoStore()
    store.restore([{"id": "db", "content": "Older DB", "status": "pending"}], revision=3)
    history_state = {
        "todos": [{"id": "history", "content": "Newer history", "status": "in_progress"}],
        "revision": 5,
    }
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("history-wins", source="tui")
        assert persist_todo_session_state(db, "history-wins", store)
        ctx = server._Resume("rid", {}, "history-wins")
        ctx.db = db

        record = ctx.record("tui", str(tmp_path), _todo_history(history_state))

    assert record["todo_state"] == history_state


def test_resume_record_prefers_newer_persisted_todo_revision(tmp_path):
    from hermes_state import SessionDB
    from tools.todo_tool import persist_todo_session_state, TodoStore

    store = TodoStore()
    store.restore([{"id": "db", "content": "Newer DB", "status": "in_progress"}], revision=9)
    history_state = {
        "todos": [{"id": "history", "content": "Older history", "status": "pending"}],
        "revision": 5,
    }
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("db-wins", source="tui")
        assert persist_todo_session_state(db, "db-wins", store)
        ctx = server._Resume("rid", {}, "db-wins")
        ctx.db = db

        record = ctx.record("tui", str(tmp_path), _todo_history(history_state))

    assert record["todo_state"] == store.snapshot()


def _durable_todo_turn(user_text: str, call_id: str, state: dict) -> list[dict]:
    return [
        {"role": "user", "content": user_text},
        {
            "role": "assistant",
            "content": "Updating the plan.",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "todo_list", "arguments": "{}"},
            }],
            "finish_reason": "tool_calls",
        },
        {
            "role": "tool",
            "tool_name": "todo_list",
            "tool_call_id": call_id,
            "content": json.dumps(state),
        },
        {"role": "assistant", "content": f"Finished {user_text}.", "finish_reason": "stop"},
    ]


def _todo_rewind_session(db, session_key: str, current_state: dict) -> dict:
    from tools.todo_tool import TodoStore, persist_todo_session_state

    store = TodoStore()
    store.restore(current_state["todos"], revision=current_state["revision"])
    assert persist_todo_session_state(db, session_key, store)
    return {
        "agent": types.SimpleNamespace(_todo_store=store),
        "history": db.get_messages_as_conversation(session_key, include_row_ids=True),
        "history_lock": threading.Lock(),
        "history_version": 0,
        "session_key": session_key,
        "todo_state": current_state,
    }


def test_rewind_restores_historical_todo_contents_with_newer_revision(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    first_state = {
        "todos": [{"id": "first", "content": "First plan", "status": "in_progress"}],
        "revision": 1,
    }
    current_state = {
        "todos": [{"id": "future", "content": "Future plan", "status": "in_progress"}],
        "revision": 2,
    }
    events = []
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session(
            "rewind-todo", source="tui", model_config={"provider": "openrouter", "keep": "yes"}
        )
        db.append_messages_batch(
            "rewind-todo",
            _durable_todo_turn("first request", "todo-1", first_state)
            + _durable_todo_turn("second request", "todo-2", current_state),
        )
        session = _todo_rewind_session(db, "rewind-todo", current_state)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: events.append((event, sid, payload)))

        with session["history_lock"]:
            server._rewind_active_session_history(session, 1, runtime_sid="runtime-rewind")

        persisted = db.get_session_model_config_value("rewind-todo", "_todo_state")
        row_config = server._parse_model_config(db.get_session("rewind-todo")["model_config"])
        active = db.get_messages_as_conversation("rewind-todo")

    expected = {"todos": first_state["todos"], "revision": 3}
    assert persisted == expected
    assert session["agent"]._todo_store.snapshot() == expected
    assert session["todo_state"] == expected
    assert row_config["provider"] == "openrouter" and row_config["keep"] == "yes"
    assert all(message.get("content") != "second request" for message in active)
    assert events == [("todo.updated", "runtime-rewind", expected)]


def test_rewind_recovers_pre_compaction_todo_snapshot(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    pre_compaction = {
        "todos": [{"id": "old", "content": "Pre-compression plan", "status": "in_progress"}],
        "revision": 1,
    }
    current_state = {
        "todos": [{"id": "new", "content": "Post-compression plan", "status": "in_progress"}],
        "revision": 2,
    }
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("compacted-todo", source="tui")
        db.append_messages_batch(
            "compacted-todo", _durable_todo_turn("before compression", "todo-old", pre_compaction)
        )
        db.archive_and_compact(
            "compacted-todo",
            [{"role": "assistant", "content": "[CONTEXT COMPACTION] earlier work summarized"}],
        )
        db.append_messages_batch(
            "compacted-todo", _durable_todo_turn("after compression", "todo-new", current_state)
        )
        session = _todo_rewind_session(db, "compacted-todo", current_state)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_emit", lambda *args, **kwargs: None)

        with session["history_lock"]:
            server._rewind_active_session_history(session, 0, runtime_sid="runtime-compacted")

        persisted = db.get_session_model_config_value("compacted-todo", "_todo_state")
        visible_history = db.get_messages_as_conversation(
            "compacted-todo", include_compacted=True
        )
        active_history = db.get_messages_as_conversation("compacted-todo")

    expected = {"todos": pre_compaction["todos"], "revision": 3}
    assert persisted == expected
    assert session["agent"]._todo_store.snapshot() == expected
    assert session["todo_state"] == expected
    assert any(message.get("content") == "before compression" for message in visible_history)
    assert all(message.get("content") != "after compression" for message in active_history)


def test_prompt_truncate_rewinds_todo_in_same_replace_transaction(tmp_path, monkeypatch):
    from agent.context_compressor import user_originated_turn_view
    from hermes_state import SessionDB

    first_state = {
        "todos": [{"id": "first", "content": "First plan", "status": "in_progress"}],
        "revision": 1,
    }
    current_state = {
        "todos": [{"id": "future", "content": "Future plan", "status": "in_progress"}],
        "revision": 2,
    }
    events = []
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("truncate-todo", source="tui", model_config={"keep": "yes"})
        db.append_messages_batch(
            "truncate-todo",
            _durable_todo_turn("first request", "todo-1", first_state)
            + _durable_todo_turn("second request", "todo-2", current_state),
        )
        session = _todo_rewind_session(db, "truncate-todo", current_state)
        users = [
            message for message in session["history"]
            if user_originated_turn_view(message) is not None
        ]
        second_row_id = users[1]["_row_id"]
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: events.append((event, sid, payload)))

        with session["history_lock"]:
            err, _fields = server._truncate_history_for_submit(
                "rid",
                "runtime-truncate",
                session,
                {
                    "truncate_before_user_ordinal": 1,
                    "truncate_before_row_id": second_row_id,
                    "confirm_truncate": True,
                },
                None,
            )

        persisted = db.get_session_model_config_value("truncate-todo", "_todo_state")
        row_config = server._parse_model_config(db.get_session("truncate-todo")["model_config"])
        active = db.get_messages_as_conversation("truncate-todo")

    expected = {"todos": first_state["todos"], "revision": 3}
    assert err is None
    assert persisted == expected
    assert session["agent"]._todo_store.snapshot() == expected
    assert session["todo_state"] == expected
    assert row_config["keep"] == "yes"
    assert all(message.get("content") != "second request" for message in active)
    assert events == [("todo.updated", "runtime-truncate", expected)]


def test_rewind_before_first_todo_persists_monotonic_authoritative_clear(tmp_path, monkeypatch):
    from hermes_state import SessionDB

    current_state = {
        "todos": [{"id": "future", "content": "Only future plan", "status": "in_progress"}],
        "revision": 1,
    }
    events = []
    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("rewind-clear", source="tui")
        db.append_messages_batch(
            "rewind-clear", _durable_todo_turn("first request", "todo-clear", current_state)
        )
        session = _todo_rewind_session(db, "rewind-clear", current_state)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: events.append((event, sid, payload)))

        with session["history_lock"]:
            server._rewind_active_session_history(session, 0, runtime_sid="runtime-clear")

        persisted = db.get_session_model_config_value("rewind-clear", "_todo_state")

    expected = {"todos": [], "revision": 2}
    assert persisted == expected
    assert session["agent"]._todo_store.snapshot() == expected
    assert session["todo_state"] == expected
    assert events == [("todo.updated", "runtime-clear", expected)]


def test_current_branch_inherits_newest_todo_state_but_historical_branch_does_not(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tools.todo_tool import TodoStore, persist_todo_session_state

    db_store = TodoStore()
    db_store.restore([{"id": "db", "content": "DB task", "status": "pending"}], revision=7)
    live_state = {
        "todos": [{"id": "live", "content": "Live task", "status": "in_progress"}],
        "revision": 9,
    }
    visible_history = [{"role": "user", "content": "branch here"}]
    session = {"agent": None, "todo_state": live_state}
    monkeypatch.setattr(server, "_resolve_model", lambda: "test/model")

    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.create_session("parent", source="tui")
        assert persist_todo_session_state(db, "parent", db_store)

        current = server._branch_todo_state(
            db, session, "parent", visible_history, historical=False
        )
        historical = server._branch_todo_state(
            db, session, "parent", visible_history, historical=True
        )
        assert current == live_state
        assert historical is None

        server._persist_branch(
            db, "current-child", "parent", "Current", visible_history,
            source="tui", cwd=str(tmp_path), profile_name="default", todo_state=current,
        )
        server._persist_branch(
            db, "historical-child", "parent", "Historical", visible_history,
            source="tui", cwd=str(tmp_path), profile_name="default", todo_state=historical,
        )

        assert db.get_session_model_config_value("current-child", "_todo_state") == live_state
        assert db.get_session_model_config_value("historical-child", "_todo_state") is None


def test_subagent_lifecycle_bypasses_tool_progress_off(monkeypatch):
    """Subagent rows feed the Desktop status stack / TUI spawn tree — application state, not
    tool-progress chrome — so display.tool_progress=off must not swallow them."""
    sid = "subagent-progress-off"
    events = []
    monkeypatch.setitem(server._sessions, sid, {"agent": None, "tool_progress_mode": "off"})
    monkeypatch.setattr(server, "_tool_progress_enabled", lambda _sid: False)
    monkeypatch.setattr(server, "_emit", lambda event, event_sid, payload=None: events.append(event))

    server._on_tool_progress(sid, "subagent.start", "delegate_task", "goal", None, goal="goal", subagent_id="s1")
    server._on_tool_progress(sid, "reasoning.available", "_thinking", "hmm", None)

    assert events == ["subagent.start"]
