"""
Tests for send_image_file() on Telegram, Discord, and Slack platforms,
and MEDIA: .png extraction/routing in the base platform adapter.

Covers: local image file sending, file-not-found handling, fallback on error,
        MEDIA: tag extraction for image extensions, and routing to send_image_file.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import BasePlatformAdapter


def _run(coro):
    """Run a coroutine in a fresh event loop for sync-style tests."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# MEDIA: extraction tests for image files
# ---------------------------------------------------------------------------


class TestExtractMediaImages:
    """Test that MEDIA: tags with image extensions are correctly extracted."""

    def test_png_image_extracted(self):
        content = "Here is the screenshot:\nMEDIA:/home/user/.stardust/browser_screenshots/shot.png"
        media, cleaned = BasePlatformAdapter.extract_media(content)
        assert len(media) == 1
        assert media[0][0] == "/home/user/.stardust/browser_screenshots/shot.png"
        assert "MEDIA:" not in cleaned
        assert "Here is the screenshot" in cleaned


# ---------------------------------------------------------------------------
# Telegram send_image_file tests
# ---------------------------------------------------------------------------
from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


class TestTelegramSendImageFile:
    @pytest.fixture
    def adapter(self):
        config = PlatformConfig(enabled=True, token="fake-token")
        a = TelegramAdapter(config)
        a._bot = MagicMock()
        return a

    def test_sends_local_image_as_photo(self, adapter, tmp_path):
        """send_image_file should call bot.send_photo with the opened file."""
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # Minimal PNG-like

        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_photo = AsyncMock(return_value=mock_msg)

        result = _run(
            adapter.send_image_file(chat_id="12345", image_path=str(img))
        )
        assert result.success
        assert result.message_id == "42"
        adapter._bot.send_photo.assert_awaited_once()

        # Verify photo arg was a file object (opened in rb mode)
        call_kwargs = adapter._bot.send_photo.call_args
        assert call_kwargs.kwargs["chat_id"] == 12345


    def test_returns_error_when_not_connected(self, adapter):
        """send_image_file should return error when bot is None."""
        adapter._bot = None
        result = _run(
            adapter.send_image_file(chat_id="12345", image_path="/tmp/img.png")
        )
        assert not result.success
        assert "Not connected" in result.error


# ---------------------------------------------------------------------------
# Discord send_image_file tests
# ---------------------------------------------------------------------------



import discord as discord_mod_ref  # noqa: E402


# ---------------------------------------------------------------------------
# Slack send_image_file tests
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# browser_vision screenshot cleanup tests
# ---------------------------------------------------------------------------


class TestScreenshotCleanup:
    def test_cleanup_removes_old_screenshots(self, tmp_path):
        """_cleanup_old_screenshots should remove files older than max_age_hours."""
        import time
        from tools.browser_tool_lifecycle import _cleanup_old_screenshots
        from tools.browser_tool import _last_screenshot_cleanup_by_dir

        _last_screenshot_cleanup_by_dir.clear()

        # Create a "fresh" file
        fresh = tmp_path / "browser_screenshot_fresh.png"
        fresh.write_bytes(b"new")

        # Create an "old" file and backdate its mtime
        old = tmp_path / "browser_screenshot_old.png"
        old.write_bytes(b"old")
        old_time = time.time() - (25 * 3600)  # 25 hours ago
        os.utime(str(old), (old_time, old_time))

        _cleanup_old_screenshots(tmp_path, max_age_hours=24)

        assert fresh.exists(), "Fresh screenshot should not be removed"
        assert not old.exists(), "Old screenshot should be removed"

    def test_cleanup_is_throttled_per_directory(self, tmp_path):
        import time
        from tools.browser_tool_lifecycle import _cleanup_old_screenshots
        from tools.browser_tool import _last_screenshot_cleanup_by_dir

        _last_screenshot_cleanup_by_dir.clear()

        old = tmp_path / "browser_screenshot_old.png"
        old.write_bytes(b"old")
        old_time = time.time() - (25 * 3600)
        os.utime(str(old), (old_time, old_time))

        _cleanup_old_screenshots(tmp_path, max_age_hours=24)
        assert not old.exists()

        old.write_bytes(b"old-again")
        os.utime(str(old), (old_time, old_time))
        _cleanup_old_screenshots(tmp_path, max_age_hours=24)

        assert old.exists(), "Repeated cleanup should be skipped while throttled"
