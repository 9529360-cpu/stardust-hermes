from pathlib import Path

import pytest

from hermes_cli import assistant_permissions as permissions
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools import background_task_graph as graph
from toolsets import resolve_toolset


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    identities = getattr(kb, "_INITIALIZED_FILE_IDENTITIES", None)
    if identities is not None:
        identities.pop(str(db_path.resolve()), None)
    kb.init_db()
    return home


def _plan():
    return {
        "tasks": [
            {"key": "convert", "title": "Convert PDF", "body": "Convert the supplied PDF to Word."},
            {"key": "develop", "title": "Develop feature", "body": "Implement the requested feature."},
            {
                "key": "reply",
                "title": "Reply to Wang",
                "body": "Prepare the reply after the converted document is ready.",
                "depends_on": ["convert"],
            },
        ]
    }


def test_normalize_graph_accepts_forward_references_and_stable_parallel_order():
    specs, topo = graph._normalize_tasks([
        {"key": "reply", "title": "Reply", "depends_on": ["convert"]},
        {"key": "develop", "title": "Develop"},
        {"key": "convert", "title": "Convert"},
    ])
    assert set(specs) == {"reply", "develop", "convert"}
    # develop/convert are both initially ready and keep caller order; reply opens only after convert.
    assert topo == ["develop", "convert", "reply"]


def test_normalize_graph_rejects_unknown_and_cyclic_dependencies():
    with pytest.raises(ValueError, match="unknown task"):
        graph._normalize_tasks([
            {"key": "reply", "title": "Reply", "depends_on": ["missing"]},
        ])
    with pytest.raises(ValueError, match="cyclic"):
        graph._normalize_tasks([
            {"key": "a", "title": "A", "depends_on": ["b"]},
            {"key": "b", "title": "B", "depends_on": ["a"]},
        ])


def test_start_graph_creates_parallel_ready_nodes_and_dependency_wait(kanban_home):
    result = graph.start_graph(
        _plan(), {}, default_assignee="default", created_by="desktop-main"
    )
    by_key = {item["key"]: item for item in result["tasks"]}

    assert result["ok"] is True
    assert result["count"] == 3
    assert by_key["convert"]["status"] == "ready"
    assert by_key["develop"]["status"] == "ready"
    assert by_key["reply"]["status"] == "todo"
    assert by_key["reply"]["depends_on"] == ["convert"]

    with kbc.connect_closing() as conn:
        link = conn.execute(
            "SELECT parent_id, child_id FROM task_links WHERE child_id = ?",
            (by_key["reply"]["task_id"],),
        ).fetchone()
        assert link is not None
        assert link["parent_id"] == by_key["convert"]["task_id"]


def test_final_report_fans_in_every_leaf_for_one_combined_completion(kanban_home):
    plan = _plan() | {"final_report": {}}
    result = graph.start_graph(plan, {}, default_assignee="default", created_by="desktop-main")
    by_key = {item["key"]: item for item in result["tasks"]}

    assert result["count"] == 4
    assert result["final_task_key"] == "final-report"
    assert result["final_task_id"] == by_key["final-report"]["task_id"]
    assert by_key["final-report"]["status"] == "todo"
    # convert is not a leaf because reply already depends on it. The reporter waits on the two
    # terminal branches, so it cannot run until both the reply branch and development branch finish.
    assert by_key["final-report"]["depends_on"] == ["develop", "reply"]

    with kbc.connect_closing() as conn:
        parent_rows = conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id",
            (result["final_task_id"],),
        ).fetchall()
        assert {row["parent_id"] for row in parent_rows} == {
            by_key["develop"]["task_id"], by_key["reply"]["task_id"],
        }


def test_final_report_key_conflict_is_rejected_before_writes(kanban_home):
    plan = {
        "tasks": [{"key": "final-report", "title": "Already used"}],
        "final_report": {},
    }
    with pytest.raises(ValueError, match="conflicts"):
        graph.start_graph(plan, {}, default_assignee="default", created_by="desktop-main")
    with kbc.connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0


def test_graph_creation_rolls_back_every_node_when_one_node_is_invalid(kanban_home):
    bad_plan = {
        "tasks": [
            {"key": "first", "title": "First valid task"},
            # Toolset names are not valid skill bundle names; create_task rejects this after
            # the first node was inserted inside the outer transaction.
            {"key": "second", "title": "Invalid task", "skills": ["web"]},
        ]
    }
    with pytest.raises(ValueError, match="toolset name"):
        graph.start_graph(bad_plan, {}, default_assignee="default", created_by="desktop-main")

    with kbc.connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0


def test_graph_idempotency_reuses_exact_retry_nodes(kanban_home):
    plan = _plan() | {"idempotency_key": "request-123"}
    first = graph.start_graph(plan, {}, default_assignee="default", created_by="desktop-main")
    second = graph.start_graph(plan, {}, default_assignee="default", created_by="desktop-main")

    first_ids = {item["key"]: item["task_id"] for item in first["tasks"]}
    second_ids = {item["key"]: item["task_id"] for item in second["tasks"]}
    assert second_ids == first_ids
    with kbc.connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 3


def test_graph_notification_mode_reports_subscription_truth(kanban_home, monkeypatch):
    outcomes = iter([True, False, True])
    monkeypatch.setattr("tools.kanban_tools._maybe_auto_subscribe", lambda conn, task_id: next(outcomes))
    result = graph.start_graph(
        _plan(), {}, default_assignee="default", created_by="desktop-main"
    )
    assert result["notification_mode"] == "partial"
    assert result["subscribed_count"] == 2


def test_graph_tool_is_high_level_orchestration_surface():
    assert "background_task_graph" in resolve_toolset("assistant_orchestration")
    assert permissions.classify_tool_permission("background_task_graph", {}).level == permissions.NOTIFY
    description = graph.BACKGROUND_TASK_GRAPH_SCHEMA["description"]
    assert "ordinary questions" in description
    assert "current-turn work" in description
    assert "depends_on" in description
    assert "final_report" in description
    assert "kanban" not in description.lower()
