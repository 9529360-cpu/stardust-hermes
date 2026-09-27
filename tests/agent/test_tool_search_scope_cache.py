"""Regression tests for Agent-side Tool Search scope caching."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.tool_executor import _tool_search_scoped_names


def _tool_def(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_scope_cache_tracks_session_surface_not_live_defer_config():
    agent = SimpleNamespace(
        enabled_toolsets=["hermes-cli"],
        disabled_toolsets=[],
        valid_tool_names={"tool_search", "tool_describe", "tool_call", "read_file"},
    )
    defs = [_tool_def("read_file"), _tool_def("todo_list")]

    with (
        patch("model_tools.get_tool_definitions", return_value=defs) as get_defs,
        patch(
            "tools.tool_search.load_config_readonly",
            side_effect=AssertionError("session scope must not reread defer config"),
        ),
    ):
        assert _tool_search_scoped_names(agent) == frozenset({"todo_list"})
        assert _tool_search_scoped_names(agent) == frozenset({"todo_list"})
        assert get_defs.call_count == 1

        # A new session/tool-surface snapshot would expose todo_list directly.
        # Changing the surface identity invalidates the cache without consulting
        # the process-global config.
        agent.valid_tool_names.add("todo_list")
        assert _tool_search_scoped_names(agent) == frozenset()
        assert get_defs.call_count == 2
