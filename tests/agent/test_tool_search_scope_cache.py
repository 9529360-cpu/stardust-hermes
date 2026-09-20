"""Regression tests for Agent-side Tool Search scope caching."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.tool_executor import _tool_search_scoped_names
from tools.tool_search import ToolSearchConfig


def test_scope_cache_invalidates_when_defer_policy_changes():
    agent = SimpleNamespace(enabled_toolsets=["hermes-cli"], disabled_toolsets=[])

    first = ToolSearchConfig.from_raw({"defer": ["todo_list"]})
    second = ToolSearchConfig.from_raw({"defer": []})
    policies = iter((first, second))
    seen = []

    def fake_scoped(_defs, defer_tools=None):
        snapshot = frozenset(defer_tools or ())
        seen.append(snapshot)
        return snapshot & {"todo_list"}

    with (
        patch("tools.tool_search.load_config_readonly", side_effect=lambda: next(policies)),
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("tools.tool_search.scoped_deferrable_names", side_effect=fake_scoped),
    ):
        assert _tool_search_scoped_names(agent) == frozenset({"todo_list"})
        assert _tool_search_scoped_names(agent) == frozenset()

    assert seen == [first.effective_defer_tools, second.effective_defer_tools]
