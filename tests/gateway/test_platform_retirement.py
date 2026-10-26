"""Exercise platform retirement through real config loading and dispatch boundaries."""

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig, load_gateway_config


def test_legacy_platform_config_is_preserved_but_never_connected(caplog):
    config = GatewayConfig.from_dict({"platforms": {
        "signal": {"enabled": True, "token": "existing-user-token", "extra": {"http_url": "http://localhost:8080"}},
        "ntfy": {"enabled": True, "extra": {"topic": "existing-topic"}},
    }})
    assert not config.platforms[Platform.SIGNAL].enabled
    assert not config.platforms[Platform("ntfy")].enabled
    assert config.to_dict()["platforms"]["signal"]["token"] == "existing-user-token"
    assert config.get_connected_platforms() == []
    assert "no longer supported" in caplog.text


def test_real_loader_ignores_retired_env_and_keeps_supported_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    for key, value in {
        "SIGNAL_HTTP_URL": "http://localhost:8080", "SIGNAL_ACCOUNT": "+15551234567",
        "QQ_APP_ID": "retired-app", "QQ_CLIENT_SECRET": "retired-secret",
        "DISCORD_BOT_TOKEN": "retired-token", "TELEGRAM_BOT_TOKEN": "123456789:real-looking-test-token",
        "WHATSAPP_ENABLED": "true", "WEIXIN_TOKEN": "real-looking-weixin-token",
        "WEIXIN_ACCOUNT_ID": "test-account", "WHATSAPP_CLOUD_PHONE_NUMBER_ID": "12345",
        "WHATSAPP_CLOUD_ACCESS_TOKEN": "real-looking-cloud-token",
    }.items():
        monkeypatch.setenv(key, value)
    config = load_gateway_config()
    assert Platform.SIGNAL not in config.platforms
    assert Platform.QQBOT not in config.platforms
    assert Platform.DISCORD not in config.platforms
    for platform in (Platform.TELEGRAM, Platform.WHATSAPP, Platform.WEIXIN, Platform.WHATSAPP_CLOUD):
        assert config.platforms[platform].enabled
    assert config.platforms[Platform.WEIXIN].extra["account_id"] == "test-account"
    assert config.platforms[Platform.WHATSAPP_CLOUD].extra["phone_number_id"] == "12345"


@pytest.mark.asyncio
async def test_retired_direct_send_is_explicitly_rejected():
    from tools.send_message_tool import _send_to_platform
    result = await _send_to_platform(Platform.SIGNAL, PlatformConfig(enabled=True), "recipient", "hello")
    assert "no longer supported" in result["error"]


def test_retained_native_adapter_factories_construct_cloud_and_weixin():
    from gateway.run import _instantiate_builtin_adapter
    from gateway.platforms.whatsapp_cloud import WhatsAppCloudAdapter
    from gateway.platforms.weixin import WeixinAdapter

    cloud = _instantiate_builtin_adapter(
        Platform.WHATSAPP_CLOUD,
        PlatformConfig(enabled=True, extra={"phone_number_id": "12345", "access_token": "cloud-test-token"}),
    )
    weixin = _instantiate_builtin_adapter(
        Platform.WEIXIN,
        PlatformConfig(enabled=True, token="weixin-test-token", extra={"account_id": "test-account"}),
    )

    assert isinstance(cloud, WhatsAppCloudAdapter)
    assert isinstance(weixin, WeixinAdapter)



def test_plugin_discovery_preserves_supported_adapter_factories(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    from hermes_cli.plugins import discover_plugins
    from gateway.platform_registry import platform_registry
    discover_plugins()
    for name in ("telegram", "whatsapp"):
        entry = platform_registry.get(name)
        assert entry is not None
        assert callable(entry.adapter_factory)


@pytest.mark.asyncio
async def test_retired_native_config_keeps_explicit_relay_front_delivery(monkeypatch):
    from types import SimpleNamespace
    from gateway.delivery import resolve_delivery_transport
    from gateway.relay.adapter import RelayAdapter
    from gateway.relay.descriptor import CONTRACT_VERSION, CapabilityDescriptor
    import tools.send_message_tool as sender

    class Transport:
        _identities = [("slack", "test-bot")]

        def __init__(self):
            self.frames = []

        async def send_outbound(self, payload, platform=None):
            self.frames.append((platform, payload))
            return {"success": True, "message_id": "relay-message"}

    transport = Transport()
    config = GatewayConfig.from_dict({"platforms": {
        "slack": {"enabled": True, "token": "old-native-token"},
        "relay": {"enabled": True, "extra": {"relay_url": "http://localhost:9999"}},
    }})
    descriptor = CapabilityDescriptor(
        contract_version=CONTRACT_VERSION, platform="slack", label="Relay",
        max_message_length=4096, supports_draft_streaming=False, supports_edit=True,
        supports_threads=True, markdown_dialect="plain", len_unit="chars",
    )
    relay = RelayAdapter(config.platforms[Platform.RELAY], descriptor, transport=transport)
    runner = SimpleNamespace(_gateway_loop=None)
    monkeypatch.setattr(sender, "_live_adapter", lambda platform: (runner, relay))
    selected = resolve_delivery_transport(Platform.SLACK, config, {Platform.RELAY: relay})
    assert selected.is_relay
    platform, pconfig, _, error = sender._resolve_platform_config("slack", config)
    assert error is None and pconfig.token is None
    result = await sender._send_to_platform(platform, pconfig, "C123", "hello")
    assert result["success"]
    assert transport.frames[0][0] == "slack"
    assert transport.frames[0][1]["content"] == "hello"
    config.platforms[Platform.RELAY].enabled = False
    assert "no longer supported" in sender._resolve_platform_config("slack", config)[3]
