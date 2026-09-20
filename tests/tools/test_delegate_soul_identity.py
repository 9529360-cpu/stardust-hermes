"""Regression coverage for delegated Stardust identity isolation."""

from unittest.mock import MagicMock, patch

from tests.tools.test_delegate import _make_mock_parent
from tools.delegate_tool import _build_child_agent


def test_delegated_child_loads_soul_but_keeps_context_and_memory_isolated():
    parent = _make_mock_parent(depth=0)

    with patch("tools.delegate_tool._load_config", return_value={}), patch("run_agent.AIAgent") as mock_agent:
        mock_agent.return_value = MagicMock()
        _build_child_agent(
            task_index=0,
            goal="identity wiring",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            task_count=1,
            parent_agent=parent,
        )

    kwargs = mock_agent.call_args.kwargs
    assert kwargs["platform"] == "subagent"
    assert kwargs["skip_context_files"] is True
    assert kwargs["load_soul_identity"] is True
    assert kwargs["skip_memory"] is True
