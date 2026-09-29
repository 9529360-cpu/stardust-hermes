"""Unfinished work must not disappear behind a bounded window of completed work."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools import assistant_tasks


def _task(task_id, *, status="ready", created=1, started=None, completed=None, owner="local"):
    return SimpleNamespace(
        id=task_id, title=task_id, status=status, assistant_owner_key=owner,
        created_at=created, started_at=started, completed_at=completed,
        assignee="default", project_id=None, workspace_kind="scratch", workspace_path=None,
        block_kind="needs_input" if status == "blocked" else None,
        last_failure_error=None, result=None,
    )


def _activity(task):
    instant = next(value for value in (task.completed_at, task.started_at, task.created_at) if value is not None)
    return instant, task.created_at, task.id


@pytest.fixture
def recall_board(monkeypatch):
    rows, calls, hydrated = [], [], []

    def list_tasks(_conn, **kwargs):
        calls.append(kwargs)
        selected = [task for task in rows if task.assistant_owner_key == kwargs["assistant_owner_key"]]
        if not kwargs.get("include_archived"):
            selected = [task for task in selected if task.status != "archived"]
        if kwargs.get("status") is not None:
            selected = [task for task in selected if task.status == kwargs["status"]]
        return sorted(selected, key=_activity, reverse=True)[:kwargs["limit"]]

    def latest_run(_conn, task_id):
        hydrated.append(task_id)
        return None

    kb = SimpleNamespace(
        list_tasks=list_tasks, latest_run=latest_run, list_attachments=lambda *_: [],
        get_task=lambda _conn, task_id: next((task for task in rows if task.id == task_id), None),
    )

    @contextmanager
    def board(_slug):
        yield kb, None

    monkeypatch.setattr("tools.kanban_tools._board", board)
    monkeypatch.setattr(assistant_tasks, "_assistant_board_slugs", lambda: ["default"])
    monkeypatch.setattr(assistant_tasks, "_assistant_tasks_context_allowed", lambda: True)
    return rows, calls, hydrated


def _recall(**kwargs):
    return json.loads(assistant_tasks.assistant_tasks_tool(action="list", owner_key="local", **kwargs))


def test_unfinished_recall_filters_before_the_database_limit(recall_board):
    from hermes_cli.kanban_db import VALID_STATUSES

    rows, calls, hydrated = recall_board
    unfinished = VALID_STATUSES - {"done", "archived"}
    rows.extend(_task(f"old-{status}", status=status) for status in sorted(unfinished))
    rows.extend(_task(f"done-{i}", status="done", completed=1000 + i) for i in range(220))
    rows.extend([
        _task("cancelled", status="archived", completed=9999),
        _task("someone-elses-work", owner="messaging-route:other", created=9999),
    ])

    result = _recall(include_completed=False, limit=20)

    assert result["partial"] is False
    assert {row["status"] for row in result["tasks"]} == unfinished
    assert {row["task_id"] for row in result["tasks"]} == {f"old-{status}" for status in unfinished}
    assert result["has_more"] is False
    assert all(call["status"] in unfinished and call["limit"] <= 21 for call in calls)
    assert all(task_id.startswith("old-") for task_id in hydrated)
    assert next(row for row in result["tasks"] if row["status"] == "blocked")["needs_attention"] is True


@pytest.mark.parametrize("include_completed", [False, True])
def test_recall_reports_more_work_without_unbounded_reads(recall_board, include_completed):
    rows, calls, _ = recall_board
    rows.extend(_task(f"task-{i}", created=10 + i) for i in range(8))

    result = _recall(include_completed=include_completed, limit=3)

    assert [row["task_id"] for row in result["tasks"]] == ["task-7", "task-6", "task-5"]
    assert result["count"] == 3
    assert result["has_more"] is True
    assert all(call["limit"] <= 4 for call in calls)


def test_global_merge_matches_database_activity_tiebreakers(recall_board):
    rows, _, _ = recall_board
    # Equal start times must retain the DB's created_at ordering, not switch to task ID.
    rows.extend([
        _task("z-older", created=1, started=100),
        _task("a-newer", status="blocked", created=2, started=100),
        _task("zero-is-an-instant", created=200, started=0),
    ])

    result = _recall(include_completed=False, limit=2)

    assert [row["task_id"] for row in result["tasks"]] == ["a-newer", "z-older"]
    assert result["has_more"] is True


def test_explicit_ids_preserve_owner_and_terminal_filters(recall_board):
    rows, calls, _ = recall_board
    rows.extend([
        _task("active"), _task("done", status="done", completed=5),
        _task("other", owner="messaging-route:other"),
    ])

    active = _recall(include_completed=False, task_ids=["active", "done", "other"], limit=5)
    all_owned = _recall(include_completed=True, task_ids=["active", "done", "other"], limit=1)

    assert [row["task_id"] for row in active["tasks"]] == ["active"]
    assert active["has_more"] is False
    assert [row["task_id"] for row in all_owned["tasks"]] == ["done"]
    assert all_owned["has_more"] is True
    assert calls == []


def test_unreadable_board_is_partial_not_all_clear(recall_board, monkeypatch):
    rows, _, _ = recall_board
    rows.append(_task("still-pending"))
    from tools import kanban_tools

    readable_board = kanban_tools._board

    @contextmanager
    def board(slug):
        if slug == "unreadable":
            raise OSError("private error text")
        with readable_board(slug) as context:
            yield context

    monkeypatch.setattr(kanban_tools, "_board", board)
    monkeypatch.setattr(assistant_tasks, "_assistant_board_slugs", lambda: ["default", "unreadable"])
    result = _recall(include_completed=False)

    assert result["partial"] is True
    assert result["board_errors"] == [{"board": "unreadable", "error": "OSError"}]
    assert [row["task_id"] for row in result["tasks"]] == ["still-pending"]
    assert "private error text" not in json.dumps(result)


def test_status_changes_between_queries_do_not_duplicate_a_task(recall_board, monkeypatch):
    rows, _, _ = recall_board
    rows.append(_task("same-work"))
    from tools import kanban_tools

    original_board = kanban_tools._board

    @contextmanager
    def changing_board(slug):
        with original_board(slug) as (kb, conn):
            # One row can be observed in two status queries if another writer moves it.
            monkeypatch.setattr(kb, "list_tasks", lambda *_args, **_kwargs: rows)
            yield kb, conn

    monkeypatch.setattr(kanban_tools, "_board", changing_board)
    result = _recall(include_completed=False)

    assert [row["task_id"] for row in result["tasks"]] == ["same-work"]
    assert result["has_more"] is False


@pytest.fixture
def real_recall_home(tmp_path, monkeypatch):
    from gateway.session_context import clear_session_vars, set_session_vars

    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(name, raising=False)
    tokens = set_session_vars(source="cli", platform="cli", cron_session="0")
    try:
        yield home
    finally:
        clear_session_vars(tokens)


def _inline_recall(**kwargs):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext

    return json.loads(INLINE_TOOL_EXECUTORS["assistant_tasks"](
        SimpleNamespace(session_id="recall-session"), {"action": "list", **kwargs},
        InlineToolContext(effective_task_id="recall-turn", tool_call_id="recall-call"),
    ))


def test_real_storage_keeps_old_unfinished_work_visible(real_recall_home):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    unfinished = kb.VALID_STATUSES - {"done", "archived"}
    with kbc.connect() as conn:
        for index, status in enumerate(sorted(unfinished) + ["done"] * 220 + ["archived"]):
            task_id = kb.create_task(conn, title=f"work-{index}", assignee="default", assistant_owner_key="local")
            # Fixture states only: the test exercises recall, not worker transitions.
            conn.execute(
                "UPDATE tasks SET status = ?, created_at = ?, completed_at = ?, block_kind = ? WHERE id = ?",
                (status, 100 + index, 2000 + index if status in {"done", "archived"} else None,
                 "needs_input" if status == "blocked" else None, task_id),
            )
        conn.commit()
        before = [tuple(row) for row in conn.execute("SELECT id, status, created_at, completed_at FROM tasks ORDER BY id")]

    result = _inline_recall(include_completed=False, limit=20)

    assert result["partial"] is False
    assert {row["status"] for row in result["tasks"]} == unfinished
    assert result["count"] == len(unfinished)
    assert result["has_more"] is False
    with kbc.connect() as conn:
        after = [tuple(row) for row in conn.execute("SELECT id, status, created_at, completed_at FROM tasks ORDER BY id")]
        assert after == before


def test_real_storage_merges_active_boards_without_crossing_owners(real_recall_home):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    for slug, offset in (("alpha", 0), ("beta", 10)):
        kb.create_board(slug, name=slug.title())
        with kbc.connect_closing(board=slug) as conn:
            for index in range(5):
                task_id = kb.create_task(conn, title=f"{slug}-{index}", assignee="default", assistant_owner_key="local")
                conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (100 + offset + index, task_id))
            other_id = kb.create_task(conn, title="private-other-owner", assignee="default", assistant_owner_key="messaging-route:other")
            conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (9999, other_id))
            conn.commit()

    result = _inline_recall(include_completed=False, limit=3)

    assert result["partial"] is False
    assert [row["title"] for row in result["tasks"]] == ["beta-4", "beta-3", "beta-2"]
    assert {row["board"] for row in result["tasks"]} == {"beta"}
    assert result["has_more"] is True
    assert "private-other-owner" not in json.dumps(result)
