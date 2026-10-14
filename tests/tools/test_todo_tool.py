"""Tests for the todo tool module."""

import json

from tools.todo_tool import (
    TODO_SESSION_STATE_KEY,
    TodoStore,
    load_todo_session_state,
    persist_todo_session_state,
    todo_tool,
)


class TestWriteAndRead:
    def test_write_replaces_list(self):
        store = TodoStore()
        items = [
            {"id": "1", "content": "First task", "status": "pending"},
            {"id": "2", "content": "Second task", "status": "in_progress"},
        ]
        result = store.write(items)
        assert len(result) == 2
        assert result[0]["id"] == "2"
        assert result[0]["status"] == "in_progress"
        assert result[1]["id"] == "1"


    def test_write_deduplicates_duplicate_ids(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "First version", "status": "pending"},
            {"id": "2", "content": "Other task", "status": "pending"},
            {"id": "1", "content": "Latest version", "status": "in_progress"},
        ])
        assert result == [
            {"id": "1", "content": "Latest version", "status": "in_progress"},
            {"id": "2", "content": "Other task", "status": "pending"},
        ]

    def test_write_moves_active_item_before_earlier_pending_step(self):
        store = TodoStore()
        result = store.write([
            {"id": "1", "content": "Already done", "status": "completed"},
            {"id": "2", "content": "Verify freed space", "status": "pending"},
            {"id": "3", "content": "Move archives to Trash", "status": "in_progress"},
        ])
        assert result == [
            {"id": "1", "content": "Already done", "status": "completed"},
            {"id": "3", "content": "Move archives to Trash", "status": "in_progress"},
            {"id": "2", "content": "Verify freed space", "status": "pending"},
        ]

    def test_multiple_active_items_keep_first_and_report_normalization(self):
        store = TodoStore()
        result = json.loads(todo_tool(todos=[
            {"id": "1", "content": "Current task", "status": "in_progress"},
            {"id": "2", "content": "Next task", "status": "in_progress"},
        ], store=store))

        assert [(item["id"], item["status"]) for item in result["todos"]] == [
            ("1", "in_progress"),
            ("2", "pending"),
        ]
        assert result["summary"]["in_progress"] == 1
        assert result["warnings"] and "Only one todo" in result["warnings"][0]


class TestHasItems:
    def test_empty_store(self):
        store = TodoStore()
        assert store.has_items() is False

    def test_non_empty_store(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "x", "status": "pending"}])
        assert store.has_items() is True


class TestFormatForInjection:
    def test_empty_returns_none(self):
        store = TodoStore()
        assert store.format_for_injection() is None

    def test_non_empty_has_markers(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Do thing", "status": "completed"},
            {"id": "2", "content": "Next", "status": "pending"},
            {"id": "3", "content": "Working", "status": "in_progress"},
        ])
        text = store.format_for_injection()
        # Completed items are filtered out of injection
        assert "[x]" not in text
        assert "Do thing" not in text
        # Active items are included
        assert "[ ]" in text
        assert "[>]" in text
        assert "Next" in text
        assert "Working" in text
        assert "context compression" in text.lower()

    def test_blocked_task_keeps_its_prerequisite_after_compression(self):
        store = TodoStore()
        store.write([
            {"id": "setup", "content": "Prepare fixture", "status": "pending"},
            {"id": "verify", "content": "Run focused check", "status": "in_progress", "depends_on": ["setup"]},
        ])

        text = store.format_for_injection()

        assert "verify" in text
        assert "waiting for: setup" in text


class TestMergeMode:
    def test_update_existing_by_id(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Original", "status": "pending"},
        ])
        store.write(
            [{"id": "1", "status": "completed"}],
            merge=True,
        )
        items = store.read()
        assert len(items) == 1
        assert items[0]["status"] == "completed"
        assert items[0]["content"] == "Original"

    def test_merge_appends_new(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "First", "status": "pending"}])
        store.write(
            [{"id": "2", "content": "Second", "status": "pending"}],
            merge=True,
        )
        items = store.read()
        assert len(items) == 2

    def test_merge_can_set_and_clear_dependencies(self):
        store = TodoStore()
        store.write([
            {"id": "setup", "content": "Prepare", "status": "pending"},
            {"id": "build", "content": "Build", "status": "pending"},
        ])

        items = store.write([{"id": "build", "depends_on": ["setup"]}], merge=True)
        assert next(item for item in items if item["id"] == "build")["depends_on"] == ["setup"]

        items = store.write([{"id": "build", "depends_on": []}], merge=True)
        assert "depends_on" not in next(item for item in items if item["id"] == "build")


class TestDependencyGating:
    def test_incomplete_dependency_blocks_activation_and_reports_reason(self):
        store = TodoStore()
        result = json.loads(todo_tool(todos=[
            {"id": "setup", "content": "Prepare fixture", "status": "pending"},
            {"id": "verify", "content": "Run focused check", "status": "in_progress", "depends_on": ["setup"]},
        ], store=store))

        by_id = {item["id"]: item for item in result["todos"]}
        assert by_id["verify"]["status"] == "pending"
        assert result["warnings"] and "verify" in result["warnings"][0]
        assert "setup" in result["warnings"][0]

        result = json.loads(todo_tool(todos=[
            {"id": "setup", "status": "completed"},
            {"id": "verify", "status": "in_progress"},
        ], merge=True, store=store))
        assert {item["id"]: item["status"] for item in result["todos"]}["verify"] == "in_progress"

    def test_missing_dependency_stays_blocked(self):
        store = TodoStore()
        result = json.loads(todo_tool(todos=[
            {"id": "verify", "content": "Run focused check", "status": "in_progress", "depends_on": ["missing"]},
        ], store=store))

        assert result["todos"][0]["status"] == "pending"
        assert "missing" in result["warnings"][0]

    def test_merge_reorders_active_item_ahead_of_earlier_pending_step(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Completed", "status": "completed"},
            {"id": "2", "content": "Verify freed space", "status": "pending"},
            {"id": "3", "content": "Move archives to Trash", "status": "pending"},
        ])
        result = store.write(
            [{"id": "3", "status": "in_progress"}],
            merge=True,
        )
        assert result == [
            {"id": "1", "content": "Completed", "status": "completed"},
            {"id": "3", "content": "Move archives to Trash", "status": "in_progress"},
            {"id": "2", "content": "Verify freed space", "status": "pending"},
        ]

    def test_merge_does_not_activate_a_second_item(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "Current task", "status": "in_progress"},
            {"id": "2", "content": "Next task", "status": "pending"},
        ])

        result = json.loads(todo_tool(
            todos=[{"id": "2", "status": "in_progress"}], merge=True, store=store,
        ))

        by_id = {item["id"]: item for item in result["todos"]}
        assert by_id["1"]["status"] == "in_progress"
        assert by_id["2"]["status"] == "pending"
        assert result["warnings"]


class TestTodoToolFunction:
    def test_read_mode(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "pending"}])
        result = json.loads(todo_tool(store=store))
        assert result["summary"]["total"] == 1
        assert result["summary"]["pending"] == 1
        assert result["revision"] == 1


    def test_no_store_returns_error(self):
        result = json.loads(todo_tool())
        assert "error" in result


class TestTodoStoreSnapshots:
    def test_restore_normalizes_multiple_active_items(self):
        store = TodoStore()
        items = store.restore([
            {"id": "1", "content": "Current task", "status": "in_progress"},
            {"id": "2", "content": "Next task", "status": "in_progress"},
        ], revision=7)

        assert [item["status"] for item in items] == ["in_progress", "pending"]
        assert store.snapshot()["revision"] == 7

    def test_restore_does_not_resume_a_task_with_unfinished_dependencies(self):
        store = TodoStore()
        items = store.restore([
            {"id": "setup", "content": "Prepare fixture", "status": "pending"},
            {"id": "verify", "content": "Run check", "status": "in_progress", "depends_on": ["setup"]},
        ], revision=7)

        assert items[1]["status"] == "pending"
        assert items[1]["depends_on"] == ["setup"]
        assert store.snapshot()["revision"] == 7

    def test_revision_only_advances_when_state_changes(self):
        store = TodoStore()
        items = [{"id": "1", "content": "Task", "status": "pending"}]

        store.write(items)
        first = store.snapshot()
        store.write(items)

        assert first["revision"] == 1
        assert store.snapshot() == first

    def test_restore_adopts_a_trusted_revision(self):
        store = TodoStore()
        store.restore(
            [{"id": "1", "content": "Task", "status": "pending"}], revision=7
        )

        assert store.snapshot()["revision"] == 7

        store.write([{"id": "1", "content": "Task", "status": "completed"}])
        assert store.snapshot()["revision"] == 8


class TestTodoSessionStatePersistence:
    def test_persist_and_load_roundtrip(self, tmp_path):
        from hermes_state import SessionDB

        store = TodoStore()
        store.write([
            {"id": "1", "content": "Keep task alive", "status": "in_progress"},
            {"id": "2", "content": "Verify result", "status": "pending", "depends_on": ["1"]},
        ])
        with SessionDB(db_path=tmp_path / "state.db") as db:
            db.create_session("todo-session", source="test")

            assert persist_todo_session_state(db, "todo-session", store) is True
            restored = load_todo_session_state(db, "todo-session")

        assert restored == store.snapshot()

    def test_stale_revision_cannot_overwrite_newer_persisted_state(self, tmp_path):
        from hermes_state import SessionDB

        newer = TodoStore()
        newer.restore(
            [{"id": "new", "content": "New plan", "status": "in_progress"}], revision=7
        )
        stale = TodoStore()
        stale.restore(
            [{"id": "old", "content": "Old plan", "status": "pending"}], revision=4
        )
        with SessionDB(db_path=tmp_path / "state.db") as db:
            db.create_session("todo-monotonic", source="test")
            assert persist_todo_session_state(db, "todo-monotonic", newer) is True
            assert persist_todo_session_state(db, "todo-monotonic", stale) is False
            restored = load_todo_session_state(db, "todo-monotonic")

        assert restored == newer.snapshot()

    def test_equal_revision_divergence_is_a_conflict_not_last_writer_wins(self, tmp_path):
        from hermes_state import SessionDB

        first = TodoStore()
        first.restore([{"id": "a", "content": "First", "status": "pending"}], revision=3)
        rival = TodoStore()
        rival.restore([{"id": "b", "content": "Rival", "status": "pending"}], revision=3)
        with SessionDB(db_path=tmp_path / "state.db") as db:
            db.create_session("todo-conflict", source="test")
            assert persist_todo_session_state(db, "todo-conflict", first) is True
            assert persist_todo_session_state(db, "todo-conflict", rival) is False
            restored = load_todo_session_state(db, "todo-conflict")

        assert restored == first.snapshot()

    def test_equal_identical_revision_is_idempotent_success(self, tmp_path):
        from hermes_state import SessionDB

        store = TodoStore()
        store.restore([{"id": "1", "content": "Same", "status": "pending"}], revision=2)
        clone = TodoStore()
        clone.restore(store.read(), revision=2)
        with SessionDB(db_path=tmp_path / "state.db") as db:
            db.create_session("todo-idempotent", source="test")
            assert persist_todo_session_state(db, "todo-idempotent", store) is True
            assert persist_todo_session_state(db, "todo-idempotent", clone) is True

        assert clone.snapshot() == store.snapshot()

    def test_empty_list_is_an_authoritative_persisted_clear(self, tmp_path):
        from hermes_state import SessionDB

        store = TodoStore()
        store.write([{"id": "1", "content": "Old task", "status": "pending"}])
        store.write([])
        assert store.snapshot()["revision"] == 2
        with SessionDB(db_path=tmp_path / "state.db") as db:
            db.create_session("todo-clear", source="test")
            assert persist_todo_session_state(db, "todo-clear", store) is True

            raw = db.get_session_model_config_value("todo-clear", TODO_SESSION_STATE_KEY)
            restored = load_todo_session_state(db, "todo-clear")

        assert raw["revision"] == 2
        assert raw["todos"] == []
        assert restored == {"todos": [], "revision": 2}


class TestTodoStoreBounds:
    """Bounds on persisted todo state (GHSA-5g4g-6jrg-mw3g hardening).

    The todo list is re-injected into context after every compression event,
    so an unbounded item — whether authored by the model or replayed from
    caller-supplied history on the API server's _hydrate_todo_store path —
    would defeat the compression it rides through. These pin the caps.
    Not a security boundary (the API surface is authenticated and the caller
    supplies their own history); this is footgun containment / parity.
    """

    def test_oversized_content_is_truncated(self):
        from tools.todo_tool import MAX_TODO_CONTENT_CHARS
        store = TodoStore()
        store.write([{"id": "1", "content": "A" * 50001, "status": "pending"}])
        item = store.read()[0]
        assert len(item["content"]) <= MAX_TODO_CONTENT_CHARS
        assert item["content"].endswith("… [truncated]")

    def test_injection_block_is_bounded(self):
        from tools.todo_tool import MAX_TODO_CONTENT_CHARS
        store = TodoStore()
        store.write([{"id": "1", "content": "A" * 50001, "status": "pending"}])
        inj = store.format_for_injection()
        # Before the fix this was ~50085 chars; now it tracks the cap.
        assert len(inj) < MAX_TODO_CONTENT_CHARS + 200


    def test_item_count_is_bounded(self):
        from tools.todo_tool import MAX_TODO_ITEMS
        store = TodoStore()
        store.write([
            {"id": str(i), "content": f"task {i}", "status": "pending"}
            for i in range(5000)
        ])
        assert len(store.read()) == MAX_TODO_ITEMS

    def test_normal_list_is_unchanged(self):
        """No regression: ordinary plans pass through untouched (no marker,
        same content, same order)."""
        store = TodoStore()
        store.write([
            {"id": "1", "content": "write the report", "status": "in_progress"},
            {"id": "2", "content": "review PR", "status": "pending"},
        ])
        items = store.read()
        assert [i["content"] for i in items] == ["write the report", "review PR"]
        assert "[truncated]" not in items[0]["content"]
