import model_tools

from tools.background_task import _check_background_task_mode
from tools.kanban_toolset_context import (
    assistant_orchestration_requested,
    kanban_toolset_requested,
    scoped_kanban_toolset_selection,
)
from tools.kanban_tools import _check_kanban_orchestrator_mode


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


def test_selection_context_keeps_assistant_and_kernel_capabilities_independent():
    assert assistant_orchestration_requested() is None
    assert kanban_toolset_requested() is None

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
