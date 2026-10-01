"""Habits for working in a Windows user's desktop apps (field checkup 2026-10-01).

On the reporting machine the agent looked for Excel on PATH, concluded it was missing and re-drew a
PDF by hand, and closed an Excel it could not click with `taskkill /F`, a habit that also kills
every other workbook open in that Excel process.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.windows_only
def test_windows_host_hints_cover_finding_office_and_closing_apps():
    from agent.prompt_builder import _local_host_hints

    text = "\n".join(_local_host_hints())

    assert "New-Object -ComObject Excel.Application" in text
    assert "taskkill /F" in text


def test_tool_sessions_are_told_to_say_when_they_took_another_route():
    from agent.system_prompt import build_system_prompt_parts

    agent = SimpleNamespace(
        load_soul_identity=False, skip_context_files=True, valid_tool_names={"terminal"},
        _task_completion_guidance=True, _tool_use_enforcement=False, _environment_probe=False,
        _kanban_worker_guidance="", _memory_store=None, _memory_manager=None, model="", provider="",
        platform="", pass_session_id=False, session_id="", _emit_status=lambda *_a, **_k: None)
    with patch("agent.prompt_builder.build_environment_hints", return_value=""):
        stable = build_system_prompt_parts(agent)["stable"]

    assert "a different route" in stable
