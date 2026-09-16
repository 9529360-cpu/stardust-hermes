"""Tests for shared-metrics send configuration resolution."""

from __future__ import annotations

import logging

import pytest

from hermes_cli.config import DEFAULT_CONFIG
from hermes_cli.observability.shared_metrics_send_config import (
    DEFAULT_ENDPOINT,
    LEGACY_UPSTREAM_ENDPOINT,
    resolve_send_config,
    reset_warning_latch_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_latch():
    reset_warning_latch_for_tests()
    yield
    reset_warning_latch_for_tests()


def _config(**shared):
    return {"telemetry": {"shared_metrics": shared}}


class TestDefaults:
    def test_send_is_registered_disabled_by_default(self):
        shared = DEFAULT_CONFIG["telemetry"]["shared_metrics"]
        assert shared["enabled"] is False
        assert shared["send"] is False

    def test_stardust_has_no_implicit_remote_endpoint(self):
        assert DEFAULT_ENDPOINT == ""
        assert DEFAULT_CONFIG["telemetry"]["shared_metrics"]["endpoint"] == ""

    def test_legacy_hermes_endpoint_is_not_stardust_authority(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint=LEGACY_UPSTREAM_ENDPOINT)
        )

        assert resolved.endpoint == LEGACY_UPSTREAM_ENDPOINT
        assert resolved.send is False

    def test_empty_config_sends_nothing(self):
        resolved = resolve_send_config({})
        assert resolved.enabled is False
        assert resolved.send is False

    def test_none_config_is_tolerated(self):
        assert resolve_send_config(None).send is False


class TestSendRequiresCollection:
    def test_collection_alone_does_not_send(self):
        resolved = resolve_send_config(_config(enabled=True))
        assert resolved.enabled is True
        assert resolved.send is False

    def test_send_with_collection_and_explicit_endpoint_sends(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint="https://metrics.stardust.invalid/v1")
        )
        assert resolved.send is True

    def test_send_without_collection_is_refused(self):
        resolved = resolve_send_config(_config(enabled=False, send=True))
        assert resolved.send is False
        # send must never imply enabled
        assert resolved.enabled is False

    def test_send_without_collection_logs_an_error(self, caplog):
        with caplog.at_level(logging.ERROR):
            resolve_send_config(_config(enabled=False, send=True))
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert "enabled is false" in errors[0].getMessage()

    def test_the_error_is_logged_once_per_process(self, caplog):
        with caplog.at_level(logging.ERROR):
            for _ in range(5):
                resolve_send_config(_config(enabled=False, send=True))
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1, "misconfiguration must not spam every hook fire"


class TestEndpointPrecedence:
    def test_config_endpoint_is_used(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint="https://example.test/v1")
        )
        assert resolved.endpoint == "https://example.test/v1"

    def test_no_environment_variable_can_redirect_telemetry(self, monkeypatch):
        """A consent hazard: an inherited env var must not silently retarget.

        AGENTS.md also reserves HERMES_* for secrets, not behaviour.
        """
        for name in (
            "HERMES_TELEMETRY_ENDPOINT",
            "TELEMETRY_ENDPOINT",
            "HERMES_SHARED_METRICS_ENDPOINT",
        ):
            monkeypatch.setenv(name, "https://attacker.test/v1")
        resolved = resolve_send_config(_config(enabled=True, send=True))
        assert resolved.endpoint == DEFAULT_ENDPOINT
        assert resolved.send is False

    def test_blank_endpoint_is_refused(self, caplog):
        with caplog.at_level(logging.ERROR):
            resolved = resolve_send_config(_config(enabled=True, send=True, endpoint="   "))
        assert resolved.endpoint == DEFAULT_ENDPOINT
        assert resolved.send is False
        assert any("no default telemetry endpoint" in r.getMessage() for r in caplog.records)

    def test_legacy_upstream_endpoint_is_refused(self, caplog):
        with caplog.at_level(logging.ERROR):
            resolved = resolve_send_config(
                _config(enabled=True, send=True, endpoint=LEGACY_UPSTREAM_ENDPOINT)
            )
        assert resolved.send is False
        assert any("legacy Hermes upstream" in r.getMessage() for r in caplog.records)

    def test_endpoint_is_stripped(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint="  https://staging.test/v1  ")
        )
        assert resolved.endpoint == "https://staging.test/v1"


class TestTransportSafety:
    def test_plaintext_endpoint_is_refused(self, caplog):
        with caplog.at_level(logging.ERROR):
            resolved = resolve_send_config(
                _config(enabled=True, send=True, endpoint="http://example.test/v1")
            )
        assert resolved.send is False, "telemetry must not go out in clear text"
        assert any("https" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://localhost:8099/v1/telemetry",
            "http://127.0.0.1:8099/v1/telemetry",
        ],
    )
    def test_loopback_http_is_allowed_for_testing(self, endpoint):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint=endpoint)
        )
        assert resolved.send is True

    def test_nonsense_scheme_is_refused(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint="ftp://example.test/v1")
        )
        assert resolved.send is False

    @pytest.mark.parametrize(
        "endpoint",
        [
            "ftp://localhost/v1/telemetry",
            "gopher://localhost/v1/telemetry",
            "ws://127.0.0.1/v1/telemetry",
        ],
    )
    def test_a_non_http_scheme_on_loopback_is_still_refused(self, endpoint):
        """The scheme is allowlisted, not merely checked for plaintext http.

        Gap found by mutation testing: replacing the `http` scheme test with
        `if True` survived the whole suite, because every non-http scheme case
        pointed at a REMOTE host, where the loopback branch rejects it anyway.
        Only a non-http scheme aimed at loopback distinguishes an allowlist
        from a plaintext-only check.
        """
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint=endpoint)
        )
        assert resolved.send is False

    def test_unsafe_endpoint_does_not_block_collection(self):
        resolved = resolve_send_config(
            _config(enabled=True, send=True, endpoint="http://example.test/v1")
        )
        assert resolved.enabled is True
