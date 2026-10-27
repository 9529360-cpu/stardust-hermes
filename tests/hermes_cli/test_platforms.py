"""Supported platform menus must not advertise removed native adapters."""

from types import SimpleNamespace

from gateway.config import RETIRED_PLATFORMS
from gateway.platform_registry import platform_registry
from hermes_cli.platforms import PLATFORMS, get_all_platforms
from toolsets import get_toolset


def test_supported_menus_keep_three_messaging_platforms_and_service_contexts():
    assert {"cli", "cron", "telegram", "whatsapp", "whatsapp_cloud", "weixin",
            "webhook", "api_server"} <= PLATFORMS.keys()
    assert not RETIRED_PLATFORMS.intersection(PLATFORMS)
    for info in PLATFORMS.values():
        assert get_toolset(info.default_toolset, include_registry=False) is not None


def test_plugin_enumeration_cannot_reintroduce_retired_native_adapters(monkeypatch):
    entries = [SimpleNamespace(name=name, label=name.title(), emoji="")
               for name in ("signal", "yuanbao", "custom_channel")]
    monkeypatch.setattr(platform_registry, "plugin_entries", lambda: entries)
    platforms = get_all_platforms()
    assert "signal" not in platforms
    assert "yuanbao" not in platforms
    assert platforms["custom_channel"].default_toolset == "hermes-custom_channel"


def test_cli_gateway_status_lists_retained_messaging_platforms(monkeypatch, capsys):
    from hermes_cli.cli_info_mixin import CLIInfoMixin

    config = SimpleNamespace(platforms={}, get_home_channel=lambda _platform: None)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr("hermes_constants.display_hermes_home", lambda: "/test/stardust")

    CLIInfoMixin._show_gateway_status(object())
    output = capsys.readouterr().out

    for name in ("Telegram", "WhatsApp", "WhatsApp Cloud", "Weixin / WeChat"):
        assert name in output
    for name in ("Discord", "Slack", "DISCORD_BOT_TOKEN"):
        assert name not in output


def test_doctor_does_not_probe_removed_discord_adapter_sdk(monkeypatch):
    from hermes_cli import doctor_platform

    imports = []
    def probe(name):
        imports.append(name)
        if name == "discord":
            raise ImportError("native adapter removed")

    monkeypatch.setattr(doctor_platform, "__import__", probe, raising=False)
    doctor_platform._check_required_packages(False)
    assert "discord" not in imports
    assert {"telegram", "httpx", "openai"} <= set(imports)
