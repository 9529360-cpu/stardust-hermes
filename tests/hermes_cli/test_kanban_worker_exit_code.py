"""A Kanban worker that loses its model provider exits EX_TEMPFAIL instead of failing its task.

Field failure (2026-09-30, maintainer's machine): zdzui.xyz timed out for ~3 minutes, twice. Each
worker exited 1, the dispatcher counted two failures, gave the card up (``gave_up``, limit 2) and it
sat blocked for an hour until the user asked why nothing was happening; a rerun minutes later
succeeded. The rate-limit sentinel (``KANBAN_RATE_LIMIT_EXIT_CODE``) already requeues a worker without
counting a failure; provider *availability* failures now take the same path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli
from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
from hermes_cli import kanban_db_dispatch as dispatch


@pytest.mark.parametrize("goal_mode", [False, True])
def test_worker_uses_quiet_exit_contract_for_both_modes(monkeypatch, goal_mode):
    monkeypatch.setattr(dispatch, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(dispatch, "_resolve_worker_cli_toolsets", lambda _home: [])
    task = SimpleNamespace(id="t_exitcode", skills=(), model_override=None,
                    provider_override=None, reasoning_effort=None, goal_mode=goal_mode)
    argv = dispatch._worker_argv(task, "default", None)
    assert argv[-4:] == ["chat", "-q", "work kanban task t_exitcode", "-Q"]



def _quiet_exit_code(monkeypatch, result: dict, *, kanban_task: bool = True) -> int:
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    if kanban_task:
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_exitcode")
    else:
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    agent = SimpleNamespace(run_conversation=lambda **_kw: dict(result), session_id="s-1")
    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(
            SimpleNamespace(agent=agent, conversation_history=[], session_id="s-1"), "work kanban task t_exitcode")
    return exc.value.code


def test_failed_first_turn_skips_goal_judge_and_preserves_provider_exit(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_GOAL_MODE", "1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_exitcode")
    called = []
    monkeypatch.setattr(cli, "_run_kanban_goal_loop_q", lambda *_: called.append(True))
    agent = SimpleNamespace(run_conversation=lambda **_kw: {
        "failed": True, "failure_reason": "server_error", "error": "HTTP 503",
    }, session_id="s-1")
    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(
            SimpleNamespace(agent=agent, conversation_history=[], session_id="s-1"), "work kanban task t_exitcode")
    assert exc.value.code == KANBAN_RATE_LIMIT_EXIT_CODE
    assert not called
@pytest.mark.parametrize("reason", [
    "timeout", "overloaded", "server_error", "upstream_rate_limit",  # provider availability
    "rate_limit", "billing",  # the original quota-wall pair
])
def test_provider_unavailability_requeues_the_task(monkeypatch, reason):
    result = {"failed": True, "failure_reason": reason, "error": "provider did not answer"}
    assert _quiet_exit_code(monkeypatch, result) == KANBAN_RATE_LIMIT_EXIT_CODE


@pytest.mark.parametrize("reason", [
    "auth_permanent", "content_policy_blocked", "context_overflow", "format_error", "loop_error",
])
def test_task_level_failures_still_count_against_the_breaker(monkeypatch, reason):
    """Retrying these unchanged cannot succeed; they must reach the failure limit."""
    result = {"failed": True, "failure_reason": reason, "error": "deterministic"}
    assert _quiet_exit_code(monkeypatch, result) == 1


def test_a_plain_one_shot_run_keeps_exit_1(monkeypatch):
    """The sentinel is a dispatcher contract; scripts calling ``hermes chat -q`` still see 1."""
    result = {"failed": True, "failure_reason": "timeout", "error": "provider did not answer"}
    assert _quiet_exit_code(monkeypatch, result, kanban_task=False) == 1
