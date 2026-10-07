"""Fail-closed session capability enforcement at model-tool dispatch."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


_TOOLSET = "mcp-session-capability-regression"


@pytest.fixture
def registered_tool():
    from tools.registry import registry

    calls = []
    name = "session_capability_hidden_tool"

    def handler(args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"ok": True})

    registry.register(
        name=name,
        toolset=_TOOLSET,
        schema={
            "name": name,
            "description": "Test-only hidden capability.",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handler,
    )
    try:
        yield name, calls
    finally:
        registry.deregister(name)


def test_direct_hidden_tool_dispatch_fails_closed_without_grant(registered_tool):
    import model_tools

    name, calls = registered_tool
    result = json.loads(model_tools.handle_function_call(name, {}, session_id="session-a"))

    assert "not authorized" in result["error"]
    assert calls == []


def test_grant_dispatches_but_disabled_toolset_still_denies(registered_tool):
    import model_tools

    name, calls = registered_tool
    result = json.loads(model_tools.handle_function_call(
        name,
        {},
        session_id="session-a",
        enabled_tools=[name],
        enabled_toolsets=[_TOOLSET],
        disabled_toolsets=[_TOOLSET],
    ))

    assert "not authorized" in result["error"]
    assert calls == []

    result = json.loads(model_tools.handle_function_call(
        name,
        {},
        session_id="session-a",
        enabled_tools=[name],
        enabled_toolsets=[_TOOLSET],
    ))
    assert result == {"ok": True}
    assert len(calls) == 1


def test_connector_dispatch_requires_the_session_connections_grant(monkeypatch):
    import model_tools

    name = "connectors__gmail__SEND_EMAIL"
    with patch(
        "tools.connectors.dispatch_connector_call",
        side_effect=AssertionError("connector I/O must not run without a grant"),
    ):
        result = json.loads(model_tools.handle_function_call(name, {}, session_id="session-a"))

    assert "not authorized" in result["error"]


def test_delegated_child_does_not_inherit_parent_capability_context(registered_tool):
    import model_tools
    from agent.delegation_context import delegated_child_context

    name, calls = registered_tool
    with model_tools.tool_capability_context(allowed_tools=[name], session_id="parent"):
        with delegated_child_context("child"):
            result = json.loads(model_tools.handle_function_call(name, {}, session_id="child"))

    assert "not authorized" in result["error"]
    assert calls == []


def test_replay_requires_an_explicit_capability_grant(registered_tool):
    import model_tools

    name, calls = registered_tool
    with model_tools.tool_capability_context(allowed_tools=[name], session_id="parent"):
        result = json.loads(model_tools.handle_function_call(
            name, {}, session_id="replay", replay=True,
        ))

    assert "not authorized" in result["error"]
    assert calls == []

    result = json.loads(model_tools.handle_function_call(
        name,
        {},
        session_id="replay",
        replay=True,
        capability_context={"allowed_tools": [name], "replay": True},
    ))
    assert result == {"ok": True}
    assert len(calls) == 1


def test_bridge_unwrap_is_explicit_exception_but_direct_hidden_call_is_not(monkeypatch, registered_tool):
    import model_tools

    name, calls = registered_tool
    with patch.object(
        model_tools,
        "_dispatch_bridge_tool",
        return_value=(None, (name, {})),
    ):
        result = json.loads(model_tools.handle_function_call(
            "tool_call",
            {"name": name},
            session_id="session-a",
            enabled_tools=["tool_call"],
        ))

    assert result == {"ok": True}
    assert len(calls) == 1
