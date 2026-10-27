"""Regression tests for send_message media routing on the retained platforms."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from tools.send_message_tool import _send_to_platform


@pytest.mark.parametrize(
    ("platform", "sender"),
    [
        (Platform.TELEGRAM, "_send_telegram"),
        (Platform.WEIXIN, "_send_weixin"),
    ],
)
def test_retained_native_platforms_accept_media_only(platform, sender):
    pconfig = MagicMock()
    pconfig.token = "test-token"
    send = AsyncMock(return_value={"success": True, "media_delivered": True})

    with patch(f"tools.send_message_tool.{sender}", new=send):
        result = asyncio.run(
            _send_to_platform(platform, pconfig, "test-chat", "", media_files=[("/tmp/test.png", False)])
        )

    assert result["success"] is True
    send.assert_awaited_once()


def test_retained_whatsapp_standalone_sender_accepts_media_only():
    send = AsyncMock(return_value={"success": True, "media_delivered": True})

    with patch("tools.send_message_tool._send_plugin_standalone", new=send):
        result = asyncio.run(
            _send_to_platform(
                Platform.WHATSAPP,
                MagicMock(),
                "test-chat",
                "",
                media_files=[("/tmp/test.png", False)],
            )
        )

    assert result["success"] is True
    send.assert_awaited_once()


def test_generic_platform_rejects_media_only_delivery():
    result = asyncio.run(
        _send_to_platform(
            Platform.API_SERVER,
            MagicMock(),
            "api-chat",
            "",
            media_files=[("/tmp/test.png", False)],
        )
    )

    assert "error" in result
    assert "only supported for" in result["error"]


def test_generic_platform_media_warning_lists_retained_channels_only():
    via_adapter = AsyncMock(return_value={"success": True})

    with patch("tools.send_message_tool._via_adapter_route", new=via_adapter):
        result = asyncio.run(
            _send_to_platform(
                Platform.API_SERVER,
                MagicMock(),
                "api-chat",
                "Text with an attachment",
                media_files=[("/tmp/test.png", False)],
            )
        )

    assert result["success"] is True
    warning = " ".join(result["warnings"])
    assert all(platform in warning for platform in ("telegram", "weixin", "whatsapp"))
    assert "signal" not in warning.lower()
    via_adapter.assert_awaited_once()
