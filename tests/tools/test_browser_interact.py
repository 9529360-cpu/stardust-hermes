"""Regression coverage for the compact browser transaction interaction tool."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture()
def browser_modules(monkeypatch):
    from tools import browser_tool as bt
    from tools import browser_tool_interactions as interactions

    monkeypatch.setattr(interactions, "_backend_block_reason", lambda: None)
    monkeypatch.setattr(bt, "_blocked_private_page_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bt, "_blocked_private_page_content", lambda *_args, **_kwargs: None)
    return bt, interactions


def test_schema_keeps_advanced_actions_compact_and_excludes_file_transfer():
    from tools.browser_tool_interactions import BROWSER_INTERACT_ACTIONS, BROWSER_INTERACT_SCHEMA

    actions = set(BROWSER_INTERACT_SCHEMA["parameters"]["properties"]["action"]["enum"])

    assert actions == set(BROWSER_INTERACT_ACTIONS)
    assert {"hover", "select", "check", "uncheck", "drag", "wait_text", "wait_url", "wait_load"} <= actions
    assert "upload" not in actions
    assert "download" not in actions
    assert "path" not in BROWSER_INTERACT_SCHEMA["parameters"]["properties"]


@pytest.mark.parametrize(
    ("kwargs", "command", "argv", "payload"),
    [
        ({"action": "hover", "ref": "e1"}, "hover", ["@e1"], {"hovered": "@e1"}),
        (
            {"action": "select", "ref": "@e2", "values": ["Quiet", "Patio"]},
            "select",
            ["@e2", "Quiet", "Patio"],
            {"element": "@e2", "selected": ["Quiet", "Patio"]},
        ),
        ({"action": "check", "ref": "e3"}, "check", ["@e3"], {"element": "@e3", "checked": True}),
        (
            {"action": "uncheck", "ref": "@e4"},
            "uncheck",
            ["@e4"],
            {"element": "@e4", "checked": False},
        ),
        (
            {"action": "drag", "ref": "e5", "target_ref": "e6"},
            "drag",
            ["@e5", "@e6"],
            {"dragged": "@e5", "target": "@e6"},
        ),
        (
            {"action": "scroll_into_view", "ref": "e7"},
            "scrollintoview",
            ["@e7"],
            {"scrolled_into_view": "@e7"},
        ),
        (
            {"action": "wait_element", "ref": "e8"},
            "wait",
            ["@e8"],
            {"waited_for": "element", "selector": "@e8", "state": "visible"},
        ),
        (
            {"action": "wait_element", "ref": "#spinner", "state": "hidden"},
            "wait",
            ["#spinner", "--state", "hidden"],
            {"waited_for": "element", "selector": "#spinner", "state": "hidden"},
        ),
        (
            {"action": "wait_text", "text": "Confirmed"},
            "wait",
            ["--text", "Confirmed"],
            {"waited_for": "text", "text": "Confirmed"},
        ),
        (
            {"action": "wait_url", "url_pattern": "**/receipt"},
            "wait",
            ["--url", "**/receipt"],
            {"waited_for": "url", "url_pattern": "**/receipt"},
        ),
        (
            {"action": "wait_load", "load_state": "domcontentloaded"},
            "wait",
            ["--load", "domcontentloaded"],
            {"waited_for": "load", "load_state": "domcontentloaded"},
        ),
    ],
)
def test_interactions_map_to_state_preserving_agent_browser_commands(
    browser_modules, monkeypatch, kwargs, command, argv, payload
):
    bt, _interactions = browser_modules
    calls = []

    def fake_run(task_id, cmd, args):
        calls.append((task_id, cmd, args))
        return {"success": True, "data": {}}

    monkeypatch.setattr(bt._session, "_run_browser_command", fake_run)

    result = json.loads(bt.browser_interact(task_id="life-task", **kwargs))

    assert calls == [("life-task", command, argv)]
    assert result["success"] is True
    assert result["action"] == kwargs["action"]
    for key, value in payload.items():
        assert result[key] == value


def test_select_tolerates_single_string_from_schema_weak_clients(browser_modules, monkeypatch):
    bt, _interactions = browser_modules
    calls = []
    monkeypatch.setattr(
        bt._session,
        "_run_browser_command",
        lambda task_id, cmd, args: calls.append((task_id, cmd, args))
        or {"success": True, "data": {}},
    )

    weak_client_values: Any = "Quiet"
    result = json.loads(
        bt.browser_interact(
            action="select", ref="@e2", values=weak_client_values, task_id="life-task"
        )
    )

    assert result["success"] is True
    assert result["selected"] == ["Quiet"]
    assert calls == [("life-task", "select", ["@e2", "Quiet"])]



@pytest.mark.parametrize(
    "kwargs,error_fragment",
    [
        ({"action": "hover"}, "requires non-empty ref"),
        ({"action": "select", "ref": "@e1", "values": []}, "at least one non-empty value"),
        ({"action": "drag", "ref": "@e1"}, "requires non-empty target_ref"),
        ({"action": "wait_text"}, "requires non-empty text"),
        ({"action": "wait_url"}, "requires non-empty url_pattern"),
        ({"action": "wait_load", "load_state": "idle-ish"}, "load_state must be"),
        ({"action": "not-a-real-action"}, "Unknown browser_interact action"),
    ],
)
def test_invalid_interaction_never_touches_browser(browser_modules, monkeypatch, kwargs, error_fragment):
    bt, _interactions = browser_modules
    calls = []
    monkeypatch.setattr(
        bt._session,
        "_run_browser_command",
        lambda *args, **kw: calls.append((args, kw)) or {"success": True},
    )

    result = json.loads(bt.browser_interact(task_id="life-task", **kwargs))

    assert result["success"] is False
    assert error_fragment in result["error"]
    assert calls == []


def test_wait_rechecks_private_page_after_condition_completes(browser_modules, monkeypatch):
    bt, _interactions = browser_modules
    guards = iter([None, '{"success": false, "error": "private page blocked"}'])
    monkeypatch.setattr(bt, "_blocked_private_page_content", lambda *_args: next(guards))
    monkeypatch.setattr(
        bt._session,
        "_run_browser_command",
        lambda *_args, **_kwargs: {"success": True, "data": {}},
    )

    result = json.loads(
        bt.browser_interact(action="wait_url", url_pattern="**/done", task_id="life-task")
    )

    assert result == {"success": False, "error": "private page blocked"}


def test_backend_gate_hides_interactions_when_state_cannot_be_preserved(monkeypatch):
    from tools import browser_tool as bt
    from tools import browser_tool_interactions as interactions

    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: True)
    assert interactions.check_browser_interact_requirements() is False

    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: True)
    assert interactions.check_browser_interact_requirements() is False

    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: False)
    monkeypatch.setattr(interactions._lp, "lightpanda_engine_status", lambda: (True, "built-in lightpanda"))
    assert interactions.check_browser_interact_requirements() is False

    monkeypatch.setattr(interactions._lp, "lightpanda_engine_status", lambda: (False, ""))
    monkeypatch.setattr(interactions._install, "check_browser_requirements", lambda: True)
    assert interactions.check_browser_interact_requirements() is True


def test_registered_tool_still_routes_through_browser_controller_authority(monkeypatch):
    from tools import browser_tool as bt
    from tools.registry import registry

    calls = []

    def fake_route(action, args, *, fallback, task_id=None, session_id=None, **_kwargs):
        calls.append((action, dict(args), task_id, session_id))
        return "controller-authority"

    monkeypatch.setattr(bt, "routed_browser_handler", fake_route)
    entry = registry.get_entry("browser_interact")

    assert entry is not None
    result = entry.handler(
        {"action": "hover", "ref": "@e1"},
        task_id="life-task",
        session_id="session-1",
    )

    assert result == "controller-authority"
    assert calls == [
        ("browser_interact", {"action": "hover", "ref": "@e1"}, "life-task", "session-1")
    ]
