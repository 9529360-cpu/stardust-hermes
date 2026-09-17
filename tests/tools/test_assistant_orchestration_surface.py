import json
from pathlib import Path

import model_tools
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools.background_task import _check_background_task_mode
from tools.kanban_toolset_context import (
    assistant_orchestration_requested,
    kanban_toolset_requested,
    scoped_kanban_toolset_selection,
)
from tools.kanban_tools import _check_kanban_orchestrator_mode
from tools.registry import registry


_LOW_LEVEL_KANBAN = {
    "kanban_show",
    "kanban_list",
    "kanban_create",
    "kanban_link",
    "kanban_unblock",
    "kanban_comment",
    "kanban_attachments",
}


def _names(toolsets):
    definitions = model_tools.get_tool_definitions(
        enabled_toolsets=list(toolsets),
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    return {item["function"]["name"] for item in definitions}


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


def test_selection_context_keeps_assistant_and_kernel_capabilities_independent():
    assert assistant_orchestration_requested() is None
    assert kanban_toolset_requested() is None

    with scoped_kanban_toolset_selection(None):
        assert assistant_orchestration_requested() is False
        assert kanban_toolset_requested() is False
        assert _check_background_task_mode() is False
        assert _check_kanban_orchestrator_mode() is False

    with scoped_kanban_toolset_selection(["assistant_orchestration"]):
        assert assistant_orchestration_requested() is True
        assert kanban_toolset_requested() is False
        assert _check_background_task_mode() is True
        assert _check_kanban_orchestrator_mode() is False

    with scoped_kanban_toolset_selection(["kanban"]):
        assert assistant_orchestration_requested() is False
        assert kanban_toolset_requested() is True
        assert _check_background_task_mode() is False
        assert _check_kanban_orchestrator_mode() is True

    assert assistant_orchestration_requested() is None
    assert kanban_toolset_requested() is None


def test_assistant_schema_exposes_facades_not_low_level_kanban():
    names = _names(["assistant_orchestration"])
    assert {"background_task", "background_task_graph"} <= names
    assert names.isdisjoint(_LOW_LEVEL_KANBAN)


def test_technical_kanban_schema_keeps_kernel_tools_without_assistant_facade():
    names = _names(["kanban"])
    assert {"kanban_show", "kanban_create", "kanban_comment"} <= names
    assert "background_task" not in names
    assert "background_task_graph" not in names


def test_explicit_combined_surface_can_opt_into_both():
    names = _names(["assistant_orchestration", "kanban"])
    assert {"background_task", "background_task_graph", "kanban_show", "kanban_create"} <= names


def test_assistant_only_surface_can_still_create_durable_work_through_internal_kernel(kanban_home):
    names = _names(["assistant_orchestration"])
    assert "background_task" in names
    assert "kanban_create" not in names

    with scoped_kanban_toolset_selection(["assistant_orchestration"]):
        result = json.loads(registry.dispatch(
            "background_task",
            {"action": "start", "title": "Durable assistant work", "body": "Complete the requested work."},
        ))

    assert result["ok"] is True
    assert result["kind"] == "background_task"
    assert result["state"] == "queued"
    assert result["task_id"]
    assert "status" not in result

    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, result["task_id"])
        assert task is not None
        assert task.title == "Durable assistant work"
        assert task.status == "ready"
