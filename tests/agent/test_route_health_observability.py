from __future__ import annotations

import time

from agent import route_health
from agent.error_classifier import FailoverReason


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    route_health.reset_for_tests()


def test_health_rows_match_resolved_endpoint_when_config_omits_base_url(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure(
        "openrouter",
        "test-model",
        "https://openrouter.ai/api/v1",
        FailoverReason.timeout,
    )

    rows = route_health.health_rows("openrouter", "test-model")

    assert len(rows) == 1
    assert rows[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert rows[0]["status"] == "open"
    assert rows[0]["reason"] == "timeout"
    assert rows[0]["consecutive_failures"] == 1
    assert rows[0]["retry_after_seconds"] > 0


def test_health_rows_are_read_only_and_do_not_claim_ready_probe(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    state = route_health.snapshot()
    row = next(iter(state["routes"].values()))
    row["cooldown_until"] = time.time() - 1
    route_health._write_state(state)

    rows = route_health.health_rows("p", "m", "https://x.test")

    assert len(rows) == 1
    assert rows[0]["status"] == "probe_ready"
    # Inspection must not claim the lease. The first real caller still gets it.
    assert route_health.allow_route("p", "m", "https://x.test") == (
        True,
        0,
        "half_open_probe",
    )


def test_health_rows_normalize_corrupt_operator_fields(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    key, identity = route_health.route_identity("p", "m", "https://x.test")
    route_health._write_state(
        {
            "version": 1,
            "routes": {
                key: {
                    **identity,
                    "status": "open",
                    "cooldown_until": "not-a-number",
                    "probe_until": {"bad": "shape"},
                    "consecutive_failures": "broken",
                    "last_failure_at": ["bad"],
                }
            },
        }
    )

    rows = route_health.health_rows("p", "m")

    assert rows == [
        {
            **identity,
            "status": "probe_ready",
            "retry_after_seconds": 0,
            "reason": None,
            "consecutive_failures": 0,
            "last_failure_at": None,
            "last_success_at": None,
        }
    ]


def test_reset_state_clears_all_profile_health_rows(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p1", "m1", "https://one.test", FailoverReason.timeout)
    route_health.record_failure("p2", "m2", "https://two.test", FailoverReason.rate_limit)

    cleared = route_health.reset_state()

    assert cleared == 2
    assert route_health.snapshot() == {"version": 1, "routes": {}}
