"""Shared user-facing platform registry for Stardust."""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered for deterministic menus. Legacy gateway enum values remain readable
# without advertising removed native adapters as configurable product surfaces.
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli", PlatformInfo(label="🖥️  CLI", default_toolset="hermes-cli")),
    ("telegram", PlatformInfo(label="📱 Telegram", default_toolset="hermes-telegram")),
    ("whatsapp", PlatformInfo(label="📱 WhatsApp", default_toolset="hermes-whatsapp")),
    ("whatsapp_cloud", PlatformInfo(label="📱 WhatsApp Business (Cloud)", default_toolset="hermes-whatsapp")),
    ("weixin", PlatformInfo(label="💬 Weixin / WeChat", default_toolset="hermes-weixin")),
    ("webhook", PlatformInfo(label="🔗 Webhook", default_toolset="hermes-webhook")),
    ("api_server", PlatformInfo(label="🌐 API Server", default_toolset="hermes-api-server")),
    ("cron", PlatformInfo(label="⏰ Cron", default_toolset="hermes-cron")),
])


def _plugin_label(entry) -> str:
    return f"{entry.emoji}  {entry.label}" if entry.emoji else entry.label


def platform_label(key: str, default: str = "") -> str:
    """Return a label for a supported context or registered plugin, else *default*."""
    info = PLATFORMS.get(key)
    if info is not None:
        return info.label
    try:
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get(key)
        if entry:
            return _plugin_label(entry)
    except Exception:
        pass
    return default


def get_all_platforms() -> "OrderedDict[str, PlatformInfo]":
    """Supported contexts plus plugin platforms, excluding retired native adapters."""
    merged = OrderedDict(PLATFORMS)
    try:
        from gateway.config import RETIRED_PLATFORMS
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            if entry.name not in merged and entry.name not in RETIRED_PLATFORMS:
                merged[entry.name] = PlatformInfo(_plugin_label(entry), f"hermes-{entry.name}")
    except Exception:
        pass
    return merged
