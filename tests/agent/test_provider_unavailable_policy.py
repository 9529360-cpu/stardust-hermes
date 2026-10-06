"""Cron retries transient provider failures using the shared classifier reasons."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.error_classifier import PROVIDER_UNAVAILABLE_REASONS, FailoverReason
from cron import unreachable_retry
from cron.scheduler import _final_response_from_result


def _cron_reruns(reason: str) -> bool:
    """Whether a cron fire that failed with ``reason`` before any model call gets re-run."""
    result = {"failed": True, "completed": False, "final_response": "",
              "error": "provider did not answer", "failure_reason": reason}
    with pytest.raises(RuntimeError) as raised:
        _final_response_from_result(result, "job1", "Morning brief", None)
    return unreachable_retry.is_model_unreachable_failure(raised.value, SimpleNamespace(session_api_calls=0))


@pytest.mark.parametrize("reason", sorted(PROVIDER_UNAVAILABLE_REASONS))
def test_a_provider_that_did_not_answer_is_transient_for_cron(reason):
    assert _cron_reruns(reason)


def test_a_quota_wall_is_not_transient_for_cron():
    """A bounded retry ladder cannot resolve exhausted credit."""
    assert not _cron_reruns(FailoverReason.billing.value)


@pytest.mark.parametrize("reason", sorted(
    r.value for r in FailoverReason if r.value not in PROVIDER_UNAVAILABLE_REASONS | {FailoverReason.billing.value}
))
def test_every_other_failure_is_not_transient_for_cron(reason):
    assert not _cron_reruns(reason)
