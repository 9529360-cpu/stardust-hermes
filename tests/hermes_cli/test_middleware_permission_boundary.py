import pytest

from hermes_cli import assistant_permissions
from hermes_cli import middleware
from hermes_cli import plugins


class _Manager:
    def __init__(self, callbacks):
        self._middleware = {middleware.TOOL_EXECUTION_MIDDLEWARE: list(callbacks)}


def test_personal_assistant_execution_middleware_cannot_replace_authorized_args(monkeypatch):
    ran = []

    def rewrite(*, args, next_call, **_kwargs):
        return next_call({**args, "command": "changed-after-approval"})

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager([rewrite]))
    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)

    with pytest.raises(PermissionError, match="cannot rewrite arguments"):
        middleware.run_tool_execution_middleware(
            "terminal",
            {"command": "approved-command"},
            lambda args: ran.append(dict(args)) or {"ok": True},
        )
    assert ran == []


def test_in_place_execution_arg_mutation_is_also_detected(monkeypatch):
    ran = []

    def mutate(*, args, next_call, **_kwargs):
        args["command"] = "mutated-in-place"
        return next_call()

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager([mutate]))
    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)

    with pytest.raises(PermissionError, match="cannot rewrite arguments"):
        middleware.run_tool_execution_middleware(
            "terminal",
            {"command": "approved-command"},
            lambda args: ran.append(dict(args)) or {"ok": True},
        )
    assert ran == []


def test_personal_assistant_execution_middleware_may_wrap_unchanged_args(monkeypatch):
    seen = []

    def wrapper(*, args, next_call, **_kwargs):
        seen.append(("before", dict(args)))
        result = next_call()
        seen.append(("after", result))
        return result

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager([wrapper]))
    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)

    result = middleware.run_tool_execution_middleware(
        "terminal",
        {"command": "approved-command"},
        lambda args: {"command": args["command"]},
    )
    assert result == {"command": "approved-command"}
    assert seen[0][0] == "before"
    assert seen[1][0] == "after"


def test_legacy_non_assistant_execution_middleware_keeps_rewrite_contract(monkeypatch):
    def rewrite(*, args, next_call, **_kwargs):
        return next_call({**args, "value": 2})

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager([rewrite]))
    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: False)

    result = middleware.run_tool_execution_middleware(
        "example",
        {"value": 1},
        lambda args: dict(args),
    )
    assert result == {"value": 2}
