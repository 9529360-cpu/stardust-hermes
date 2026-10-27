"""Tests for tools/send_message_tool.py."""

import asyncio
import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# python-telegram-bot is an optional dep — skip the entire module when
# it isn't installed (e.g. CI bare env). Tests that patch telegram.Bot
# or call _send_telegram need it; tests for other platforms don't but
# keeping the whole file consistent is simpler.
try:
    import telegram
    _HAS_TELEGRAM = True
except ImportError:
    _HAS_TELEGRAM = False


from gateway.config import Platform
from tools.send_message_tool import (
    _send_telegram,
    _send_to_platform,
    send_message_tool,
)
from tools.send_message_targets import _parse_target_ref
# Discord helpers moved to the plugin in #24325.  Import from the new path
# and provide a thin ``_send_discord(token, ...)`` shim that mirrors the
# pre-migration signature so the existing test bodies keep working.


class _StreamingAiohttpContent:
    def __init__(self, body: bytes):
        self._body = body
        self.read_sizes = []

    async def read(self, size=-1):
        self.read_sizes.append(size)
        if not self._body:
            return b""
        if size is None or size < 0:
            chunk = self._body
            self._body = b""
            return chunk
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk


class _StreamingAiohttpResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.content = _StreamingAiohttpContent(body)
        self.closed = False
        self.json = AsyncMock(side_effect=AssertionError("resp.json() should not be used"))
        self.text = AsyncMock(side_effect=AssertionError("resp.text() should not be used"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def close(self):
        self.closed = True


class _StreamingAiohttpSession:
    def __init__(self, response: _StreamingAiohttpResponse):
        self.response = response
        self.post = MagicMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _make_config():
    telegram_cfg = SimpleNamespace(enabled=True, token="***", extra={})
    return SimpleNamespace(
        platforms={Platform.TELEGRAM: telegram_cfg},
        get_home_channel=lambda _platform: None,
    ), telegram_cfg


def _install_telegram_mock(monkeypatch, bot):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2", HTML="HTML")
    constants_mod = SimpleNamespace(ParseMode=parse_mode)
    # MessageEntity needed by #27865 mention-detection path; tests don't
    # inspect it but the import must succeed.
    _MessageEntity = lambda **_kw: SimpleNamespace(**_kw)
    telegram_mod = SimpleNamespace(Bot=lambda token: bot, MessageEntity=_MessageEntity, constants=constants_mod)
    monkeypatch.setitem(sys.modules, "telegram", telegram_mod)
    monkeypatch.setitem(sys.modules, "telegram.constants", constants_mod)


class TestSendMessageTool:


    def test_media_tag_outside_allowed_roots_is_not_sent(self, tmp_path, monkeypatch):
        # This test exercises the strict-allowlist path; force strict mode on
        # and disable recency trust so the freshly-written tmp_path file is
        # not auto-accepted by the trust window. (Recency trust is covered
        # in test_platform_base.py. The public default flipped to non-strict
        # in 2026-05; this test pins strict on explicitly.)
        monkeypatch.setenv("STARDUST_MEDIA_DELIVERY_STRICT", "1")
        monkeypatch.setenv("STARDUST_MEDIA_TRUST_RECENT_FILES", "0")
        config, telegram_cfg = _make_config()
        secret = tmp_path / "secret.pdf"
        secret.write_bytes(b"%PDF secret")

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
             patch("gateway.mirror.mirror_to_session", return_value=True):
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:12345",
                        "message": f"hello\nMEDIA:{secret}",
                    }
                )
            )

        assert result["success"] is True
        send_mock.assert_awaited_once_with(
            Platform.TELEGRAM,
            telegram_cfg,
            "12345",
            "hello",
            thread_id=None,
            media_files=[],
            force_document=False,
        )

    def test_top_level_send_failure_redacts_query_token(self):
        config, _telegram_cfg = _make_config()
        leaked = "very-secret-query-token-123456"

        def _raise_and_close(coro):
            coro.close()
            raise RuntimeError(
                f"transport error: https://api.example.com/send?access_token={leaked}"
            )

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("model_tools._run_async", side_effect=_raise_and_close):
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:-1001",
                        "message": "hello",
                    }
                )
            )

        assert "error" in result
        assert leaked not in result["error"]
        assert "access_token=***" in result["error"]


@pytest.mark.skipif(not _HAS_TELEGRAM, reason="python-telegram-bot not installed")
class TestSendTelegramMediaDelivery:
    def test_sends_photo_with_caption_for_media_tag(self, tmp_path, monkeypatch):
        # A single captionable image + short text now rides as the photo's
        # native caption (MEDIA:<path> caption), not a separate text message.
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=2))
        bot.send_video = AsyncMock()
        bot.send_voice = AsyncMock()
        bot.send_audio = AsyncMock()
        bot.send_document = AsyncMock()
        _install_telegram_mock(monkeypatch, bot)

        result = asyncio.run(
            _send_telegram(
                "token",
                "12345",
                "Hello there",
                media_files=[(str(image_path), False)],
            )
        )

        assert result["success"] is True
        assert result["message_id"] == "2"
        # No separate text send — the caption rides the photo bubble.
        bot.send_message.assert_not_awaited()
        bot.send_photo.assert_awaited_once()
        assert bot.send_photo.await_args.kwargs.get("caption") == "Hello there"

    def test_sends_voice_for_ogg_with_voice_directive(self, tmp_path, monkeypatch):
        voice_path = tmp_path / "voice.ogg"
        voice_path.write_bytes(b"OggS" + b"\x00" * 32)

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()
        bot.send_video = AsyncMock()
        bot.send_voice = AsyncMock(return_value=SimpleNamespace(message_id=7))
        bot.send_audio = AsyncMock()
        bot.send_document = AsyncMock()
        _install_telegram_mock(monkeypatch, bot)

        result = asyncio.run(
            _send_telegram(
                "token",
                "12345",
                "",
                media_files=[(str(voice_path), True)],
            )
        )

        assert result["success"] is True
        bot.send_voice.assert_awaited_once()
        bot.send_audio.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    def test_missing_media_returns_error_without_leaking_raw_tag(self, monkeypatch):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()
        bot.send_video = AsyncMock()
        bot.send_voice = AsyncMock()
        bot.send_audio = AsyncMock()
        bot.send_document = AsyncMock()
        _install_telegram_mock(monkeypatch, bot)

        result = asyncio.run(
            _send_telegram(
                "token",
                "12345",
                "",
                media_files=[("/tmp/does-not-exist.png", False)],
            )
        )

        assert "error" in result
        assert "No deliverable text or media remained" in result["error"]
        bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: long messages are chunked before platform dispatch
# ---------------------------------------------------------------------------


class TestSendToPlatformChunking:


    def test_telegram_markdown_expansion_is_chunked_before_send(self, monkeypatch):
        """Telegram chunking must account for MarkdownV2 escaping expansion.

        A raw message under 4096 UTF-16 units can inflate past the limit once
        MarkdownV2-escaped (each `!`/`.`/`-` becomes `\\!`/`\\.`/`\\-`). The
        send path must chunk the *formatted* text so no single send exceeds
        4096 (issue #28557).
        """
        from gateway.platforms.base import utf16_len

        send_lengths = []

        async def fake_send_message(**kwargs):
            text = kwargs["text"]
            send_lengths.append(utf16_len(text))
            if utf16_len(text) > 4096:
                raise Exception("Message is too long")
            return SimpleNamespace(message_id=len(send_lengths))

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=fake_send_message)
        bot.send_photo = AsyncMock()
        bot.send_video = AsyncMock()
        bot.send_voice = AsyncMock()
        bot.send_audio = AsyncMock()
        bot.send_document = AsyncMock()
        _install_telegram_mock(monkeypatch, bot)

        result = asyncio.run(
            _send_to_platform(
                Platform.TELEGRAM,
                SimpleNamespace(enabled=True, token="tok", extra={}),
                "123",
                "!" * 4096,  # raw 4096 -> ~8192 after MarkdownV2 escaping
            )
        )

        assert result["success"] is True
        assert bot.send_message.await_count >= 2
        assert max(send_lengths) <= 4096


# ---------------------------------------------------------------------------
# HTML auto-detection in Telegram send
# ---------------------------------------------------------------------------


class TestSendToPlatformWhatsapp:
    def test_whatsapp_routes_via_local_bridge_sender(self):
        """WhatsApp delivery routes through the plugin's registry
        standalone_sender_fn (was tools.send_message_tool._send_whatsapp
        before the #41112 plugin migration)."""
        from hermes_cli.plugins import discover_plugins
        from gateway.platform_registry import platform_registry
        discover_plugins()
        chat_id = "test-user@lid"
        async_mock = AsyncMock(return_value={"success": True, "platform": "whatsapp", "chat_id": chat_id, "message_id": "abc123"})

        wa_entry = platform_registry.get("whatsapp")
        original_sender = wa_entry.standalone_sender_fn
        wa_entry.standalone_sender_fn = async_mock
        try:
            result = asyncio.run(
                _send_to_platform(
                    Platform.WHATSAPP,
                    SimpleNamespace(enabled=True, token=None, extra={"bridge_port": 3000}),
                    chat_id,
                    "hello from hermes",
                )
            )
        finally:
            wa_entry.standalone_sender_fn = original_sender

        assert result["success"] is True
        # _registry_standalone_send passes (pconfig, chat_id, message, thread_id=None)
        async_mock.assert_awaited_once()
        _call = async_mock.await_args
        assert _call.args[1] == chat_id
        assert _call.args[2] == "hello from hermes"


@pytest.mark.skipif(not _HAS_TELEGRAM, reason="python-telegram-bot not installed")
class TestSendTelegramHtmlDetection:
    """Verify that messages containing HTML tags are sent with parse_mode=HTML
    and that plain / markdown messages use MarkdownV2."""

    def _make_bot(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        bot.send_photo = AsyncMock()
        bot.send_video = AsyncMock()
        bot.send_voice = AsyncMock()
        bot.send_audio = AsyncMock()
        bot.send_document = AsyncMock()
        return bot

    def test_html_message_uses_html_parse_mode(self, monkeypatch):
        bot = self._make_bot()
        _install_telegram_mock(monkeypatch, bot)

        asyncio.run(
            _send_telegram("tok", "123", "<b>Hello</b> world")
        )

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["parse_mode"] == "HTML"
        assert kwargs["text"] == "<b>Hello</b> world"


    def test_transient_bad_gateway_retries_text_send(self, monkeypatch):
        bot = self._make_bot()
        bot.send_message = AsyncMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                SimpleNamespace(message_id=2),
            ]
        )
        _install_telegram_mock(monkeypatch, bot)

        with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = asyncio.run(_send_telegram("tok", "123", "hello"))

        assert result["success"] is True
        assert bot.send_message.await_count == 2
        sleep_mock.assert_awaited_once()


@pytest.mark.skipif(not _HAS_TELEGRAM, reason="python-telegram-bot not installed")
class TestSendTelegramThreadIdMapping:
    """General-topic mapping in _send_telegram (issue #22267).

    Telegram forum supergroups address the General topic as
    ``message_thread_id="1"`` on incoming updates, but the Bot API rejects
    sends with ``message_thread_id=1`` ("Message thread not found"). The
    gateway adapter's ``_message_thread_id_for_send`` helper maps "1" to
    ``None`` for that reason; the standalone ``_send_telegram`` helper used
    by the ``send_message`` tool needs the same mapping.
    """

    def _make_bot(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        return bot

    def test_general_topic_thread_id_omitted(self, monkeypatch):
        """thread_id="1" must be dropped before calling the Bot API."""
        bot = self._make_bot()
        _install_telegram_mock(monkeypatch, bot)

        asyncio.run(_send_telegram("tok", "-1001234567890", "hello", thread_id="1"))

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert "message_thread_id" not in kwargs

    def test_non_general_topic_thread_id_preserved(self, monkeypatch):
        """Real forum-topic thread ids (>1) still pass through as ints."""
        bot = self._make_bot()
        _install_telegram_mock(monkeypatch, bot)

        asyncio.run(_send_telegram("tok", "-1001234567890", "hello", thread_id="17585"))

        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["message_thread_id"] == 17585

    def test_thread_not_found_retries_without_message_thread_id(self, monkeypatch):
        """When send_message raises "thread not found", retry without thread_id (#27012)."""
        bot = self._make_bot()
        _install_telegram_mock(monkeypatch, bot)

        # First call raises thread-not-found, second succeeds
        bot.send_message = AsyncMock(side_effect=[
            Exception("Bad Request: message thread not found"),
            SimpleNamespace(message_id=2),
        ])

        asyncio.run(
            _send_telegram("tok", "-1001234567890", "hello", thread_id="17585")
        )

        assert bot.send_message.await_count == 2
        # First call: should include message_thread_id=17585
        call1_kwargs = bot.send_message.await_args_list[0].kwargs
        assert call1_kwargs["message_thread_id"] == 17585
        # Second call (retry): should NOT include message_thread_id
        call2_kwargs = bot.send_message.await_args_list[1].kwargs
        assert "message_thread_id" not in call2_kwargs

    def test_thread_not_found_for_media_retries_without_message_thread_id(self, monkeypatch, tmp_path):
        """Media send with stale thread_id retries without it (#27012)."""
        bot = self._make_bot()
        # Mock send_document to fail with thread-not-found, then succeed
        bot.send_document = AsyncMock(side_effect=[
            Exception("Bad Request: message thread not found"),
            SimpleNamespace(message_id=3),
        ])
        _install_telegram_mock(monkeypatch, bot)

        # Create a test file
        test_file = tmp_path / "doc.txt"
        test_file.write_text("test content")

        asyncio.run(
            _send_telegram(
                "tok", "-1001234567890", "",
                media_files=[(str(test_file), False)],
                thread_id="17585",
            )
        )

        assert bot.send_document.await_count == 2
        # First call: should include message_thread_id=17585
        call1_kwargs = bot.send_document.await_args_list[0].kwargs
        assert call1_kwargs["message_thread_id"] == 17585
        # Second call (retry): should NOT include message_thread_id
        call2_kwargs = bot.send_document.await_args_list[1].kwargs
        assert "message_thread_id" not in call2_kwargs


# ---------------------------------------------------------------------------
# Tests for Discord thread_id support
# ---------------------------------------------------------------------------


class TestParseTargetRef:
    """_parse_target_ref extracts (chat_id, thread_id, is_explicit) per platform.

    The tables below cover every explicit target form each platform accepts,
    the forms that must fall through to directory resolution, and the
    cross-platform scoping guards (a Slack ID is not a Discord target, a
    WhatsApp JID is not a Signal target, ...).
    """

    def test_explicit_targets_round_trip_chat_and_thread(self):
        cases = [
            # Discord: snowflake, optional :thread, surrounding whitespace
            ("discord", "-1001234567890:17585", "-1001234567890", "17585"),
            ("discord", "1003724596514", "1003724596514", None),
            ("discord", "  123456:789  ", "123456", "789"),
            # Matrix: room id, room:thread-event, user MXID
            ("matrix", "!HLOQwxYGgFPMPJUSNR:matrix.org:$thread123:matrix.org",
             "!HLOQwxYGgFPMPJUSNR:matrix.org", "$thread123:matrix.org"),
            ("matrix", "!HLOQwxYGgFPMPJUSNR:matrix.org",
             "!HLOQwxYGgFPMPJUSNR:matrix.org", None),
            ("matrix", "@hermes:matrix.org", "@hermes:matrix.org", None),
            # Phone platforms: E.164 keeps its '+' for signal-cli; groups and
            # bare digits also resolve.
            ("signal", "+41791234567", "+41791234567", None),
            ("signal", "  group:abc123  ", "group:abc123", None),
            ("signal", "15551234567", "15551234567", None),
            ("sms", "+15551234567", "+15551234567", None),
            ("whatsapp", "+15551234567", "+15551234567", None),
            ("photon", "+15551234567", "+15551234567", None),
            # WhatsApp native JIDs. Regression: group (@g.us) and linked-identity
            # (@lid) JIDs matched no branch and silently fell through to the
            # configured home DM instead of the requested group.
            ("whatsapp", "120363408391911677@g.us", "120363408391911677@g.us", None),
            ("whatsapp", "19255551234@s.whatsapp.net", "19255551234@s.whatsapp.net", None),
            ("whatsapp", "149606612619433@lid", "149606612619433@lid", None),
            ("whatsapp", "status@broadcast", "status@broadcast", None),
            ("whatsapp", "120363000000000000@newsletter",
             "120363000000000000@newsletter", None),
            # Slack: channel/group/DM ids, thread ts, and user targets that the
            # caller must open as a DM.
            ("slack", "C0B0QV5434G:171.000001", "C0B0QV5434G", "171.000001"),
            ("slack", "C0B0QV5434G", "C0B0QV5434G", None),
            ("slack", "G123ABCDEF", "G123ABCDEF", None),
            ("slack", "D123ABCDEF", "D123ABCDEF", None),
            ("slack", "U123ABCDEF", "user:U123ABCDEF", None),
            ("slack", "@alice", "user_name:alice", None),
            ("slack", "  C0B0QV5434G  ", "C0B0QV5434G", None),
            # Email
            ("email", "user@example.com", "user@example.com", None),
            ("email", "first.last@example.co.uk", "first.last@example.co.uk", None),
            ("email", "user+tag@gmail.com", "user+tag@gmail.com", None),
            ("email", "  user@example.com  ", "user@example.com", None),
        ]
        for platform, target, expected_chat, expected_thread in cases:
            chat_id, thread_id, is_explicit = _parse_target_ref(platform, target)
            label = f"{platform}:{target}"
            assert is_explicit is True, label
            assert chat_id == expected_chat, label
            assert thread_id == expected_thread, label

    def test_non_explicit_targets_fall_through_to_resolution(self):
        cases = [
            ("matrix", "#general:matrix.org"),   # alias needs resolution
            ("signal", "  group:  "),            # empty group id
            ("signal", "+123"),                  # E.164 too short
            ("signal", "+1234567890123456"),     # E.164 too long
            ("signal", "+12abc4567890"),         # non-numeric
            ("signal", "+"),
            ("whatsapp", "general"),             # friendly name, not a JID
            ("slack", "W123ABCDEF"),             # workspace id is not sendable
            ("slack", "c0b0qv5434g"),            # lowercase
            ("slack", "C123"),                   # too short
            ("slack", "X0B0QV5434G"),            # unknown prefix
            ("email", "not-an-email"),
            ("email", "@example.com"),
            ("email", "user@"),
            ("email", "user@.com"),
        ]
        for platform, target in cases:
            chat_id, _, is_explicit = _parse_target_ref(platform, target)
            assert is_explicit is False, f"{platform}:{target}"

    def test_prefixes_and_suffixes_are_platform_scoped(self):
        """A form that is explicit on one platform must not leak to another."""
        cases = [
            ("telegram", "!something"),
            ("discord", "@someone"),
            ("telegram", "+15551234567"),
            ("discord", "+15551234567"),
            ("matrix", "+15551234567"),
            ("telegram", "120363408391911677@g.us"),
            ("signal", "149606612619433@lid"),
            ("discord", "C0B0QV5434G"),
            ("telegram", "C0B0QV5434G"),
            ("telegram", "user@example.com"),
            ("discord", "user@example.com"),
            ("slack", "user@example.com"),
        ]
        for platform, target in cases:
            assert _parse_target_ref(platform, target)[2] is False, f"{platform}:{target}"


# ---------------------------------------------------------------------------
# Discord media attachment support
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests for _derive_forum_thread_name
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests for _send_discord with forum channel support
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests for _send_discord forum + media multipart upload
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests for the process-local forum-probe cache
# ---------------------------------------------------------------------------


        # Only one session opened (thread creation) — no probe session this time
        # (verified by not raising from our side_effect exhaustion)


# ---------------------------------------------------------------------------
# _send_signal — chunking + 429 retry (mirrors gateway adapter behavior)
# ---------------------------------------------------------------------------






# ── _send_via_adapter standalone fallback ────────────────────────────────


class _FakePlatform:
    """Stand-in for the gateway.config.Platform enum.  Holds the .value
    attribute consulted by ``_send_via_adapter`` for registry lookups."""

    def __init__(self, value):
        self.value = value


class TestSendViaAdapterStandaloneFallback:
    """Coverage for the out-of-process plugin-platform send path.

    When the gateway runner is not in this process (e.g. ``stardust cron``
    runs separately from ``stardust gateway``), ``_send_via_adapter`` should
    fall through to the plugin's ``standalone_sender_fn`` registered on
    its ``PlatformEntry``.  Without the hook, the existing error string
    is returned (with a more helpful tail).
    """

    @staticmethod
    def _make_entry(send_fn):
        from gateway.platform_registry import PlatformEntry

        return PlatformEntry(
            name="fakeplatform",
            label="Fake",
            adapter_factory=lambda cfg: None,
            check_fn=lambda: True,
            standalone_sender_fn=send_fn,
        )


    @pytest.mark.asyncio
    async def test_standalone_sender_fn_raises_is_caught_and_formatted(self, monkeypatch):
        """Hook raises: error dict has 'Plugin standalone send failed: ...'"""
        from tools.send_message_tool import _send_via_adapter
        from gateway.platform_registry import platform_registry

        async def boom(pconfig, chat_id, message, **kwargs):
            raise ValueError("boom!")

        platform_registry.register(self._make_entry(boom))
        try:
            monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)

            result = await _send_via_adapter(
                _FakePlatform("fakeplatform"),
                SimpleNamespace(extra={}),
                "chat-1",
                "hi",
            )
        finally:
            platform_registry.unregister("fakeplatform")

        assert result == {"error": "Plugin standalone send failed: boom!"}

@pytest.mark.skipif(not _HAS_TELEGRAM, reason="python-telegram-bot not installed")
class TestSendTelegramThreadNotFoundRetry:
    """Tests for thread-not-found retry behaviour in _send_telegram (#27012)."""

    def test_text_send_retries_without_thread_id_on_thread_not_found(self):
        """When thread is not found, the text send should retry without
        message_thread_id."""
        call_args = []

        async def fake_retry(bot, *, chat_id, text, parse_mode, **kwargs):
            call_args.append(dict(kwargs, chat_id=chat_id, text=text))
            if len(call_args) == 1:
                raise Exception("Bad Request: message thread not found")
            return SimpleNamespace(message_id=42)

        async def run_test():
            with patch(
                "tools.send_message_senders._send_telegram_message_with_retry",
                fake_retry,
            ):
                # _send_telegram imports Bot locally; we only need to mock
                # the send path, not Bot itself (Bot import falls through
                # normally since python-telegram-bot is installed).
                return await _send_telegram(
                    "fake-token", "-100123", "hello from topic 17585",
                    thread_id="17585",
                )

        result = asyncio.run(run_test())
        assert result["success"] is True
        assert result["message_id"] == "42"
        assert len(call_args) == 2, f"expected 2 calls, got {len(call_args)}"
        # First call should have message_thread_id
        assert call_args[0].get("message_thread_id") is not None
        # Second call (retry) should NOT have message_thread_id
        assert "message_thread_id" not in call_args[1], \
            "retry should drop message_thread_id after thread-not-found"

    def test_disable_web_page_preview_not_leaked_to_media_sends(self):
        """disable_web_page_preview must never leak into a media send.

        A single captionable file + short text now rides as the document's
        caption (no separate text send), so the invariant to protect is that
        the captioned send_document does not inherit disable_web_page_preview
        (valid only for send_message).
        """
        media_kwargs_seen = []

        class FakeBot:
            async def send_message(self, **kwargs):
                return SimpleNamespace(message_id=1)

            async def send_document(self, **kwargs):
                media_kwargs_seen.append(kwargs)
                return SimpleNamespace(message_id=2)

        import tempfile
        media_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(b"%PDF-1.4 test content")
                media_path = tf.name

            async def run_test():
                with patch("telegram.Bot", return_value=FakeBot()):
                    return await _send_telegram(
                        "fake-token", "-100123", "check preview",
                        media_files=[(media_path, False)],
                        disable_link_previews=True,
                    )

            result = asyncio.run(run_test())
            assert result["success"] is True
            # Caption rides the document bubble.
            assert media_kwargs_seen[0].get("caption") == "check preview"
            # Media send must NOT carry disable_web_page_preview.
            assert "disable_web_page_preview" not in media_kwargs_seen[0], \
                "disable_web_page_preview leaked into send_document kwargs"
        finally:
            if media_path and os.path.exists(media_path):
                os.unlink(media_path)
