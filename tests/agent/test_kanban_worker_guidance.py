"""The Kanban worker protocol reaches dispatcher-spawned workers only.

A profile with the kanban toolset enabled (a board orchestrator, e.g. the
desktop assistant) also has ``kanban_show``. Handing it the worker protocol
("you have been assigned ONE task; call ``kanban_show()`` first") made it open
conversations with an argument-less ``kanban_show`` that always failed.
"""

from types import SimpleNamespace

import pytest

import model_tools
from agent.agent_init import _load_tools
from agent.delegation_context import DELEGATED_CHILD_ENV_MARKER, delegated_child_context
from agent.prompt_builder import KANBAN_GUIDANCE
from agent.system_prompt import _tool_guidance_block

_PROTOCOL_HEADING = "# Kanban task execution protocol"


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {"type": "object"}}}


@pytest.fixture
def loaded_agent(monkeypatch):
    """An agent whose tool snapshot includes the kanban tools (orchestrator or worker alike)."""
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv(DELEGATED_CHILD_ENV_MARKER, raising=False)
    tools = [_tool("kanban_show"), _tool("kanban_create"), _tool("terminal")]
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **_kw: list(tools))

    def load():
        agent = SimpleNamespace(quiet_mode=True)
        _load_tools(agent, enabled_toolsets=None, disabled_toolsets=None)
        return agent

    return load


def test_orchestrator_with_kanban_tools_gets_no_worker_protocol(loaded_agent):
    agent = loaded_agent()

    assert "kanban_show" in agent.valid_tool_names
    assert agent._kanban_worker_guidance == ""
    assert _PROTOCOL_HEADING not in (_tool_guidance_block(agent) or "")


def test_dispatcher_spawned_worker_gets_worker_protocol(loaded_agent, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_0123abcd")

    agent = loaded_agent()

    assert agent._kanban_worker_guidance == KANBAN_GUIDANCE
    assert _PROTOCOL_HEADING in _tool_guidance_block(agent)


def test_delegated_child_of_a_worker_gets_no_worker_protocol(loaded_agent, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_0123abcd")

    with delegated_child_context():
        agent = loaded_agent()

    assert agent._kanban_worker_guidance == ""


def test_prompt_paths_that_skip_agent_init_apply_the_same_rule(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    orchestrator = SimpleNamespace(valid_tool_names={"kanban_show", "terminal"}, _kanban_worker_guidance=None)
    assert _PROTOCOL_HEADING not in (_tool_guidance_block(orchestrator) or "")

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_0123abcd")
    worker = SimpleNamespace(valid_tool_names={"kanban_show", "terminal"}, _kanban_worker_guidance=None)
    assert _PROTOCOL_HEADING in _tool_guidance_block(worker)
