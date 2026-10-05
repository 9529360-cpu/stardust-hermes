"""One "provider did not answer" rule for both durable orchestrators.

Kanban (requeue a worker without counting a failure) and cron (re-run a fire on the 5/15/30-minute
ladder) each kept their own list of transient provider failures, and the lists drifted: until
2026-10-05 cron only recognised network failures. Both now read PROVIDER_UNAVAILABLE_REASONS, and
this contract runs every classifier reason through both real entry points.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli
from agent.error_classifier import PROVIDER_UNAVAILABLE_REASONS, FailoverReason
from cron import unreachable_retry
from cron.scheduler import _final_response_from_result
from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE


def _kanban_worker_exit(monkeypatch, reason: str) -> int:
    """Exit status of a dispatcher-spawned worker whose turn failed with ``reason``."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_policy")
    monkeypatch.delenv("HERMES_KANBAN_RUN_ID", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    result = {"failed": True, "failure_reason": reason, "error": "provider did not answer"}
    agent = SimpleNamespace(run_conversation=lambda **_kw: dict(result), session_id="s-1")
    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(SimpleNamespace(agent=agent, conversation_history=[], session_id="s-1"),
                                    "work kanban task t_policy")
    return exc.value.code


def _cron_reruns(reason: str) -> bool:
    """Whether a cron fire that failed with ``reason`` before any model call gets re-run."""
    result = {"failed": True, "completed": False, "final_response": "",
              "error": "provider did not answer", "failure_reason": reason}
    with pytest.raises(RuntimeError) as raised:
        _final_response_from_result(result, "job1", "Morning brief", None)
    return unreachable_retry.is_model_unreachable_failure(raised.value, SimpleNamespace(session_api_calls=0))


@pytest.mark.parametrize("reason", sorted(PROVIDER_UNAVAILABLE_REASONS))
def test_a_provider_that_did_not_answer_is_transient_for_kanban_and_cron(monkeypatch, reason):
    assert _kanban_worker_exit(monkeypatch, reason) == KANBAN_RATE_LIMIT_EXIT_CODE
    assert _cron_reruns(reason)


def test_a_quota_wall_requeues_kanban_but_not_cron(monkeypatch):
    """Only kanban's rate-limit cooldown can outwait exhausted credit; a 30-minute ladder cannot."""
    assert _kanban_worker_exit(monkeypatch, FailoverReason.billing.value) == KANBAN_RATE_LIMIT_EXIT_CODE
    assert not _cron_reruns(FailoverReason.billing.value)


@pytest.mark.parametrize("reason", sorted(
    r.value for r in FailoverReason if r.value not in PROVIDER_UNAVAILABLE_REASONS | {FailoverReason.billing.value}
))
def test_every_other_failure_is_a_failure_for_both(monkeypatch, reason):
    assert _kanban_worker_exit(monkeypatch, reason) == 1
    assert not _cron_reruns(reason)
