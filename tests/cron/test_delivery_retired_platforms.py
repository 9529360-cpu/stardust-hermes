"""Removed native adapters must fail before cron queues or sends a message."""

import pytest

from cron import scheduler_delivery as delivery
from gateway.config import RETIRED_PLATFORMS


@pytest.mark.parametrize("platform", sorted(RETIRED_PLATFORMS))
@pytest.mark.parametrize("target_kind", ["bare", "explicit", "origin"])
def test_retired_delivery_returns_actionable_error_before_resolution(monkeypatch, platform, target_kind):
    monkeypatch.delenv("GATEWAY_RELAY_PLATFORMS", raising=False)
    target = {"bare": platform, "explicit": f"{platform}:old-chat", "origin": "origin"}[target_kind]
    job = {"id": "retired-job", "deliver": target,
           "origin": {"platform": platform, "chat_id": "old-chat"}}

    def must_not_resolve(*args, **kwargs):
        pytest.fail("retired target reached delivery resolution")

    monkeypatch.setattr(delivery, "_resolve_delivery_targets", must_not_resolve)
    error = delivery._deliver_result(job, "saved output")
    assert platform in error
    assert "retired" in error
    assert "Telegram" in error


def test_failure_lane_and_local_opt_out_ignore_retired_origin(monkeypatch):
    job = {"id": "legacy-job", "deliver": "signal:old", "failure_deliver": "local",
           "origin": {"platform": "signal", "chat_id": "old"}}
    assert delivery._deliver_result(job, "failure", for_failure=True) is None
    assert delivery._deliver_result({**job, "deliver": "local"}, "output") is None


@pytest.mark.parametrize("platform", ["telegram", "whatsapp", "whatsapp_cloud", "weixin", "api_server", "webhook", "relay"])
def test_supported_delivery_platforms_remain_known(platform):
    assert delivery._is_known_delivery_platform(platform)


def test_retired_native_home_excluded_but_relay_front_preserved(monkeypatch):
    monkeypatch.delenv("GATEWAY_RELAY_PLATFORMS", raising=False)
    # Saved target syntax remains readable, but it is not advertised as a home
    # fallback and _deliver_result rejects native use before any send.
    assert delivery._is_known_delivery_platform("signal")
    assert "signal" not in set(delivery._iter_home_target_platforms())
    monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "signal")
    assert delivery._is_known_delivery_platform("signal")
    assert "signal" in set(delivery._iter_home_target_platforms())


def test_relay_fronted_legacy_target_reaches_existing_transport_validation(monkeypatch):
    monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "slack")
    monkeypatch.setattr(delivery, "_resolve_delivery_targets", lambda *args, **kwargs: [])
    monkeypatch.setattr(delivery, "_unresolved_delivery_outcome", lambda *args: "transport checked")
    error = delivery._deliver_result({"id": "relay-job", "deliver": "slack:channel"}, "output")
    assert error == "transport checked"


def test_retired_native_adapter_cannot_shadow_live_relay(monkeypatch):
    from types import SimpleNamespace
    from gateway.config import Platform, PlatformConfig

    monkeypatch.delenv("GATEWAY_RELAY_PLATFORMS", raising=False)
    native = object()
    relay = SimpleNamespace(fronts_platform=lambda platform: platform == Platform.SIGNAL)
    config = SimpleNamespace(platforms={Platform.SIGNAL: PlatformConfig(enabled=True)})
    resolved, error = delivery._resolve_target_transport(
        {"id": "legacy-shadow"}, Platform.SIGNAL, "signal", {},
        {Platform.SIGNAL: native, Platform.RELAY: relay}, config)
    assert error is None
    assert resolved[0].is_relay
    assert resolved[2] is relay


def test_retired_platform_with_no_live_relay_never_uses_native_fallback(monkeypatch):
    from types import SimpleNamespace
    from gateway.config import Platform, PlatformConfig

    monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "signal")
    config = SimpleNamespace(platforms={Platform.SIGNAL: PlatformConfig(enabled=True)})
    resolved, error = delivery._resolve_target_transport(
        {"id": "no-relay"}, Platform.SIGNAL, "signal", {},
        {Platform.SIGNAL: object()}, config)
    assert resolved is None
    assert "relay-fronted" in error
