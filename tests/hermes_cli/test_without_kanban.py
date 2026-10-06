"""Startup and capability contracts after retiring the built-in task board."""

import importlib


def test_runtime_surfaces_load_without_board_modules():
    for name in ("run_agent", "gateway.run", "tui_gateway.server", "cron.scheduler"):
        importlib.import_module(name)

    from hermes_cli.commands import resolve_command
    from hermes_cli.console_engine import HermesConsoleEngine

    assert resolve_command("kanban") is None
    result = HermesConsoleEngine().execute("kanban list")
    assert result.status == "error"
    assert "Unsupported" in result.output


def test_standard_toolsets_keep_delegation_scheduling_and_planning(monkeypatch):
    from model_tools import get_tool_definitions
    from tools.registry import invalidate_check_fn_cache

    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    invalidate_check_fn_cache()

    definitions = get_tool_definitions(
        enabled_toolsets=["delegation", "cronjob", "todo", "file"], quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    names = {definition["function"]["name"] for definition in definitions}
    assert {"delegate_task", "cronjob_manage", "todo_list", "read_file"} <= names
    assert "assistant_tasks" not in names
    assert not any(name.startswith("kanban_") for name in names)


def test_only_unmodified_old_default_personas_are_upgraded(tmp_path, monkeypatch):
    from hermes_cli.config import ensure_hermes_home
    from hermes_cli.default_soul import DEFAULT_SOUL_MD, _PRE_KANBAN_DEFAULT_SOUL

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    soul = tmp_path / "SOUL.md"
    soul.write_text(_PRE_KANBAN_DEFAULT_SOUL, encoding="utf-8")
    ensure_hermes_home()
    assert soul.read_text(encoding="utf-8") == DEFAULT_SOUL_MD
    assert "assistant_tasks" not in DEFAULT_SOUL_MD

    customized = _PRE_KANBAN_DEFAULT_SOUL + " Always call me Captain."
    soul.write_text(customized, encoding="utf-8")
    ensure_hermes_home()
    assert soul.read_text(encoding="utf-8") == customized
