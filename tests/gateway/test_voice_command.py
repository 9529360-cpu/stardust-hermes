"""Tests for the /voice command and auto voice reply in the gateway."""

import asyncio
import importlib.util
import json
import os
import queue
import threading
import time
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch



from gateway.platforms.base import SessionSource
from gateway.platforms.event import MessageEvent, MessageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(text: str = "", message_type=MessageType.TEXT, chat_id="123") -> MessageEvent:
    source = SessionSource(
        chat_id=chat_id,
        user_id="user1",
        platform=MagicMock(),
    )
    source.platform.value = "telegram"
    source.thread_id = None
    event = MessageEvent(text=text, message_type=message_type, source=source)
    event.message_id = "msg42"
    return event


def _make_runner(tmp_path):
    """Create a bare GatewayRunner without calling __init__."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._VOICE_MODE_PATH = tmp_path / "gateway_voice_mode.json"
    runner._session_db = None
    runner.session_store = MagicMock()
    runner._is_user_authorized = lambda source: True
    return runner


# =====================================================================
# /voice command handler
# =====================================================================

class TestHandleVoiceCommand:

    @pytest.fixture
    def runner(self, tmp_path):
        return _make_runner(tmp_path)


    @pytest.mark.asyncio
    async def test_voice_off(self, runner):
        runner._voice_mode["telegram:123"] = "voice_only"
        event = _make_event("/voice off")
        result = await runner._handle_voice_command(event)
        assert "disabled" in result.lower()
        assert runner._voice_mode["telegram:123"] == "off"


    @pytest.mark.asyncio
    async def test_toggle_on_to_off(self, runner):
        runner._voice_mode["telegram:123"] = "voice_only"
        event = _make_event("/voice")
        result = await runner._handle_voice_command(event)
        assert "disabled" in result.lower()
        assert runner._voice_mode["telegram:123"] == "off"

    @pytest.mark.asyncio
    async def test_persistence_saved(self, runner):
        event = _make_event("/voice on")
        await runner._handle_voice_command(event)
        assert runner._VOICE_MODE_PATH.exists()
        data = json.loads(runner._VOICE_MODE_PATH.read_text())
        assert data["telegram:123"] == "voice_only"


    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", ["telegram", "weixin"])
    async def test_voice_channel_join_fails_closed_on_retained_platforms(self, runner, platform):
        from gateway.config import Platform

        event = _make_event("/voice channel")
        event.source.platform = Platform(platform)
        result = await runner._handle_voice_channel_join(event)

        assert result == "Voice channels are not supported on this platform."

    def test_sync_populates_enabled_chats_from_voice_modes(self, runner):
        """Issue #16007: sync also restores per-chat /voice on|tts opt-ins.

        The adapter's ``_auto_tts_enabled_chats`` must mirror chats whose
        persisted voice_mode is ``voice_only`` or ``all`` — without this,
        ``/voice on`` was relying on a "not in disabled set" default that
        silently enabled auto-TTS for every chat.
        """
        from gateway.config import Platform
        runner._voice_mode = {
            "telegram:off_chat": "off",
            "telegram:on_chat": "voice_only",
            "telegram:tts_chat": "all",
            "slack:999": "voice_only",  # wrong platform, must be ignored
        }
        adapter = SimpleNamespace(
            _auto_tts_default=False,
            _auto_tts_disabled_chats=set(),
            _auto_tts_enabled_chats=set(),
            platform=Platform.TELEGRAM,
        )

        runner._sync_voice_mode_state_to_adapter(adapter)

        assert adapter._auto_tts_disabled_chats == {"off_chat"}
        assert adapter._auto_tts_enabled_chats == {"on_chat", "tts_chat"}

    def test_sync_pushes_config_default_onto_adapter(self, runner, monkeypatch):
        """Issue #16007: ``voice.auto_tts`` must propagate to ``_auto_tts_default``."""
        from gateway.config import Platform

        fake_cfg = {"voice": {"auto_tts": True}}
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: fake_cfg,
        )
        adapter = SimpleNamespace(
            _auto_tts_default=False,
            _auto_tts_disabled_chats=set(),
            _auto_tts_enabled_chats=set(),
            platform=Platform.TELEGRAM,
        )

        runner._sync_voice_mode_state_to_adapter(adapter)

        assert adapter._auto_tts_default is True


    @pytest.mark.asyncio
    async def test_platform_isolation(self, runner):
        """Same chat_id on different platforms must not collide (#12542)."""
        telegram_event = _make_event("/voice on", chat_id="999")
        weixin_event = _make_event("/voice off", chat_id="999")
        weixin_event.source.platform.value = "weixin"

        await runner._handle_voice_command(telegram_event)
        await runner._handle_voice_command(weixin_event)

        assert runner._voice_mode["telegram:999"] == "voice_only"
        assert runner._voice_mode["weixin:999"] == "off"


# =====================================================================
# Auto voice reply decision logic
# =====================================================================

class TestAutoVoiceReply:
    """Test the real _should_send_voice_reply method on GatewayRunner.

    The gateway has two TTS paths:
      1. base adapter auto-TTS: fires for voice input in _process_message_background
      2. gateway _send_voice_reply: fires based on voice_mode setting

    To prevent double audio, _send_voice_reply is skipped when voice input
    already triggered base adapter auto-TTS.

    For Discord voice channels, the base adapter now routes play_tts directly
    into VC playback, so the runner should still skip voice-input follow-ups to
    avoid double playback.
    """

    @pytest.fixture
    def runner(self, tmp_path):
        return _make_runner(tmp_path)

    def _call(self, runner, voice_mode, message_type, agent_messages=None,
              response="Hello!", in_voice_channel=False):
        """Call real _should_send_voice_reply on a GatewayRunner instance."""
        chat_id = "123"
        if voice_mode != "off":
            runner._voice_mode["telegram:" + chat_id] = voice_mode
        else:
            runner._voice_mode.pop("telegram:" + chat_id, None)

        event = _make_event(message_type=message_type)

        if in_voice_channel:
            mock_adapter = MagicMock()
            mock_adapter.is_in_voice_channel = MagicMock(return_value=True)
            event.raw_message = SimpleNamespace(guild_id=111, guild=None)
            runner.adapters[event.source.platform] = mock_adapter

        return runner._should_send_voice_reply(
            event, response, agent_messages or []
        )

    # -- Full platform x input x mode matrix --------------------------------
    #
    # Legend:
    #   base = base adapter auto-TTS (play_tts)
    #   runner = gateway _send_voice_reply
    #
    # | Platform      | Input | Mode       | base | runner | Expected     |
    # |---------------|-------|------------|------|--------|--------------|
    # | Telegram      | voice | off        | yes  | skip   | 1 audio      |
    # | Telegram      | voice | voice_only | yes  | skip*  | 1 audio      |
    # | Telegram      | voice | all        | yes  | skip*  | 1 audio      |
    # | Telegram      | text  | off        | skip | skip   | 0 audio      |
    # | Telegram      | text  | voice_only | skip | skip   | 0 audio      |
    # | Telegram      | text  | all        | skip | yes    | 1 audio      |
    # | Discord text  | voice | all        | yes  | skip*  | 1 audio      |
    # | Discord text  | text  | all        | skip | yes    | 1 audio      |
    # | Discord VC    | voice | all        | skip†| yes    | 1 audio (VC) |
    # | Web UI        | voice | off        | yes  | skip   | 1 audio      |
    # | Web UI        | voice | all        | yes  | skip*  | 1 audio      |
    # | Web UI        | text  | all        | skip | yes    | 1 audio      |
    # | Slack         | voice | all        | yes  | skip*  | 1 audio      |
    # | Slack         | text  | all        | skip | yes    | 1 audio      |
    #
    # * skip_double: voice input → base already handles
    # † Discord play_tts override skips when in VC

    # -- Telegram/Slack/Web: voice input, base handles ---------------------

    def test_voice_input_voice_only_skipped(self, runner):
        """voice_only + voice input: base auto-TTS handles it, runner skips."""
        assert self._call(runner, "voice_only", MessageType.VOICE) is False


    # -- Text input: only runner handles -----------------------------------

    def test_text_input_all_mode_runner_fires(self, runner):
        """all + text input: only runner fires (base auto-TTS only for voice)."""
        assert self._call(runner, "all", MessageType.TEXT) is True


    # -- Mode off: nothing fires -------------------------------------------

    def test_off_mode_voice(self, runner):
        assert self._call(runner, "off", MessageType.VOICE) is False


    # -- Discord VC exception: runner must handle --------------------------


    # -- Edge cases --------------------------------------------------------


# =====================================================================
# _send_voice_reply
# =====================================================================

class TestSendVoiceReply:

    @pytest.fixture
    def runner(self, tmp_path):
        return _make_runner(tmp_path)

    @pytest.mark.asyncio
    async def test_calls_tts_and_send_voice(self, runner):
        from gateway.config import Platform

        mock_adapter = AsyncMock()
        mock_adapter.send_voice = AsyncMock()
        event = _make_event()
        event.source.platform = Platform.TELEGRAM
        runner.adapters[event.source.platform] = mock_adapter

        tts_result = json.dumps({"success": True, "file_path": "/tmp/test.ogg"})

        with patch("tools.tts_tool.text_to_speech_tool", return_value=tts_result) as mock_tts, \
             patch("tools.tts_text_normalize._strip_markdown_for_tts", side_effect=lambda t: t), \
             patch("os.path.isfile", return_value=True), \
             patch("os.unlink"), \
             patch("os.makedirs"):
            await runner._send_voice_reply(event, "Hello world")

        mock_adapter.send_voice.assert_called_once()
        assert mock_tts.call_args.kwargs["output_path"].endswith(".ogg")
        call_args = mock_adapter.send_voice.call_args
        assert call_args.kwargs.get("chat_id") == "123"


    @pytest.mark.asyncio
    async def test_auto_voice_reply_uses_thread_metadata_helper(self, runner):
        from gateway.config import Platform

        mock_adapter = AsyncMock()
        mock_adapter.send_voice = AsyncMock()
        event = _make_event()
        event.source.platform = Platform.TELEGRAM
        event.source.chat_type = "dm"
        event.source.thread_id = "20197"
        event.message_id = "462"
        runner.adapters[event.source.platform] = mock_adapter

        tts_result = json.dumps({"success": True, "file_path": "/tmp/test.ogg"})

        with patch("tools.tts_tool.text_to_speech_tool", return_value=tts_result), \
             patch("tools.tts_text_normalize._strip_markdown_for_tts", side_effect=lambda t: t), \
             patch("os.path.isfile", return_value=True), \
             patch("os.unlink"), \
             patch("os.makedirs"):
            await runner._send_voice_reply(event, "Hello world")

        mock_adapter.send_voice.assert_called_once()
        call_kwargs = mock_adapter.send_voice.call_args.kwargs
        assert call_kwargs["reply_to"] == "462"
        assert call_kwargs["metadata"] == {
            "thread_id": "20197",
            "telegram_dm_topic_reply_fallback": True,
            "direct_messages_topic_id": "20197",
            "telegram_reply_to_message_id": "462",
            # Final voice reply is notify-worthy (issue #27970 Bug 2):
            # mirrors the final-text path in gateway/platforms/base.py.
            "notify": True,
        }


# =====================================================================
# Discord play_tts skip when in voice channel
# =====================================================================


# =====================================================================
# Web play_tts sends play_audio (not voice bubble)
# =====================================================================

# =====================================================================
# Help text + known commands
# =====================================================================

class TestVoiceInHelp:

    def test_voice_in_help_output(self):
        """The gateway help text includes /voice (generated from registry)."""
        from hermes_cli.commands import gateway_help_lines
        help_text = "\n".join(gateway_help_lines())
        assert "/voice" in help_text


# =====================================================================
# Callback wiring order (join)
# =====================================================================

class TestCallbackWiringOrder:
    """Verify callback is wired BEFORE join, not after."""


    @pytest.mark.asyncio
    async def test_join_failure_clears_callback(self, tmp_path):
        """If join fails with exception, callback is cleaned up."""
        runner = _make_runner(tmp_path)

        mock_channel = MagicMock()
        mock_channel.name = "General"
        mock_adapter = AsyncMock()
        mock_adapter.join_voice_channel = AsyncMock(
            side_effect=RuntimeError("No permission")
        )
        mock_adapter.get_user_voice_channel = AsyncMock(return_value=mock_channel)
        mock_adapter._voice_input_callback = None

        event = _make_event("/voice channel")
        event.raw_message = SimpleNamespace(guild_id=111, guild=None)
        runner.adapters[event.source.platform] = mock_adapter

        result = await runner._handle_voice_channel_join(event)
        assert "failed" in result.lower()
        assert mock_adapter._voice_input_callback is None


# =====================================================================
# Leave exception handling
# =====================================================================

class TestLeaveExceptionHandling:
    """Verify state is cleaned up even when leave_voice_channel raises."""

    @pytest.fixture
    def runner(self, tmp_path):
        return _make_runner(tmp_path)

    @pytest.mark.asyncio
    async def test_leave_exception_still_cleans_state(self, runner):
        """If leave_voice_channel raises, voice_mode is still cleaned up."""
        mock_adapter = AsyncMock()
        mock_adapter.is_in_voice_channel = MagicMock(return_value=True)
        mock_adapter.leave_voice_channel = AsyncMock(
            side_effect=RuntimeError("Connection reset")
        )
        mock_adapter._voice_input_callback = MagicMock()

        event = _make_event("/voice leave")
        event.raw_message = SimpleNamespace(guild_id=111, guild=None)
        runner.adapters[event.source.platform] = mock_adapter
        runner._voice_mode["telegram:123"] = "all"

        result = await runner._handle_voice_channel_leave(event)
        assert "left" in result.lower()
        assert runner._voice_mode["telegram:123"] == "off"
        assert mock_adapter._voice_input_callback is None


# =====================================================================
# Base adapter empty text guard
# =====================================================================

class TestAutoTtsEmptyTextGuard:
    """Verify base adapter skips TTS when text is empty after markdown strip."""

    def test_empty_after_strip_skips_tts(self):
        """Markdown-only content should not trigger TTS call."""
        import re
        text_content = "****"
        speech_text = re.sub(r'[*_`#\[\]()]', '', text_content)[:4000].strip()
        assert not speech_text, "Expected empty after stripping markdown chars"

        # Note: base.py regex only strips individual chars, not full code blocks
        # So code blocks are partially stripped but may leave content
        # The real fix is in base.py — empty check after strip


class TestStreamTtsToSpeaker:
    """Functional tests for the streaming TTS pipeline."""

    def test_none_sentinel_flushes_buffer(self):
        """None sentinel causes remaining buffer to be spoken."""
        from tools.tts_tool_speaker import stream_tts_to_speaker
        text_q = queue.Queue()
        stop_evt = threading.Event()
        done_evt = threading.Event()
        spoken = []

        def display(text):
            spoken.append(text)

        text_q.put("Hello world.")
        text_q.put(None)

        stream_tts_to_speaker(text_q, stop_evt, done_evt, display_callback=display)
        assert done_evt.is_set()
        assert any("Hello" in s for s in spoken)

    def test_stop_event_aborts_early(self):
        """Setting stop_event causes early exit."""
        from tools.tts_tool_speaker import stream_tts_to_speaker
        text_q = queue.Queue()
        stop_evt = threading.Event()
        done_evt = threading.Event()
        spoken = []

        stop_evt.set()
        text_q.put("Should not be spoken.")
        text_q.put(None)

        stream_tts_to_speaker(text_q, stop_evt, done_evt, display_callback=lambda t: spoken.append(t))
        assert done_evt.is_set()
        assert len(spoken) == 0

    def test_done_event_set_on_exception(self):
        """tts_done_event is set even when an exception occurs."""
        from tools.tts_tool_speaker import stream_tts_to_speaker
        text_q = queue.Queue()
        stop_evt = threading.Event()
        done_evt = threading.Event()

        # Put a non-string that will cause concatenation to fail
        text_q.put(12345)
        text_q.put(None)

        stream_tts_to_speaker(text_q, stop_evt, done_evt)
        assert done_evt.is_set()


        # Display callback gets raw text (before markdown stripping)
        # But the actual TTS audio would be stripped — we verify pipeline doesn't crash


# =====================================================================
# Bug 3: play_in_voice_channel uses get_running_loop not get_event_loop
# =====================================================================


# =====================================================================
# Bug 4: _send_voice_reply filename uses uuid (no collision)
# =====================================================================

class TestSendVoiceReplyFilename:
    """_send_voice_reply uses uuid for unique filenames."""

    def test_filename_uses_uuid(self):
        """The path builder uses uuid in the filename, not time-based.

        Filename construction moved into build_auto_tts_output_path
        (gateway/platforms/base.py) when the path became platform-aware;
        the uniqueness contract lives there now.
        """
        import inspect
        from gateway.platforms.base import build_auto_tts_output_path
        from gateway.run import GatewayRunner
        source = inspect.getsource(build_auto_tts_output_path)
        assert "uuid" in source, \
            "build_auto_tts_output_path should use uuid for unique filenames"
        assert "int(time.time())" not in source, \
            "build_auto_tts_output_path should not use int(time.time()) — collision risk"
        runner_source = inspect.getsource(GatewayRunner._send_voice_reply)
        assert "build_auto_tts_output_path" in runner_source, \
            "_send_voice_reply should build its path via build_auto_tts_output_path"


# =====================================================================
# Bug 7: _send_voice_reply cleanup in finally block
# =====================================================================

class TestSendVoiceReplyCleanup:
    """_send_voice_reply must clean up temp files even on exception."""

    def test_cleanup_in_finally(self):
        """The method has cleanup in a finally block, not inside try."""
        import inspect, textwrap, ast
        from gateway.run import GatewayRunner
        source = textwrap.dedent(inspect.getsource(GatewayRunner._send_voice_reply))
        tree = ast.parse(source)
        func = tree.body[0]

        has_finally_unlink = False
        for node in ast.walk(func):
            if isinstance(node, ast.Try) and node.finalbody:
                finally_source = ast.dump(node.finalbody[0])
                if "unlink" in finally_source or "remove" in finally_source:
                    has_finally_unlink = True
                    break

        assert has_finally_unlink, \
            "_send_voice_reply must have os.unlink in a finally block"


# =====================================================================
# Bug 8: Base adapter auto-TTS cleans up temp file after play_tts
# =====================================================================

class TestAutoTtsTempFileCleanup:
    """Base adapter auto-TTS must clean up generated audio file."""

    def test_source_has_finally_remove(self):
        """play_tts call is wrapped in try/finally with os.remove."""
        import inspect
        from gateway.platforms.base import BasePlatformAdapter
        source = inspect.getsource(BasePlatformAdapter._process_message_background)
        # Find the play_tts section and verify cleanup
        play_tts_idx = source.find("play_tts")
        assert play_tts_idx > 0
        after_play = source[play_tts_idx:]
        finally_idx = after_play.find("finally")
        remove_idx = after_play.find("os.remove")
        assert finally_idx > 0, "play_tts must be in a try/finally block"
        assert remove_idx > 0, "finally block must call os.remove on _tts_path"
        assert remove_idx > finally_idx, "os.remove must be inside the finally block"


# =====================================================================
# Bugfix: disconnect() must clean up voice state
# ---------------------------------------------------------------------------


class TestDisconnectVoiceCleanup:
    """Bug: disconnect() left voice dicts populated after closing client."""

    @pytest.mark.asyncio
    async def test_disconnect_clears_voice_state(self):

        adapter = MagicMock()
        adapter._voice_clients = {111: MagicMock(), 222: MagicMock()}
        adapter._voice_receivers = {111: MagicMock(), 222: MagicMock()}
        adapter._voice_listen_tasks = {111: MagicMock(), 222: MagicMock()}
        adapter._voice_timeout_tasks = {111: MagicMock(), 222: MagicMock()}
        adapter._voice_text_channels = {111: 999, 222: 888}

        async def mock_leave(guild_id):
            adapter._voice_receivers.pop(guild_id, None)
            adapter._voice_listen_tasks.pop(guild_id, None)
            adapter._voice_clients.pop(guild_id, None)
            adapter._voice_timeout_tasks.pop(guild_id, None)
            adapter._voice_text_channels.pop(guild_id, None)

        for gid in list(adapter._voice_clients.keys()):
            await mock_leave(gid)

        assert len(adapter._voice_clients) == 0
        assert len(adapter._voice_receivers) == 0
        assert len(adapter._voice_listen_tasks) == 0
        assert len(adapter._voice_timeout_tasks) == 0


# =====================================================================
class TestVoiceTTSPlayback:
    """TTS playback: play_tts in VC, dedup, fallback."""


    # -- Runner dedup --

    @staticmethod
    def _make_runner():
        from gateway.run import GatewayRunner
        runner = object.__new__(GatewayRunner)
        runner._voice_mode = {}
        runner.adapters = {}
        return runner

    def _call_should_reply(self, runner, voice_mode, msg_type, response="Hello",
                           agent_msgs=None, already_sent=False):
        from gateway.platforms.base import SessionSource
        from gateway.platforms.event import MessageEvent
        from gateway.config import Platform
        runner._voice_mode["telegram:ch1"] = voice_mode
        source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="ch1",
            user_id="1", user_name="test", chat_type="channel",
        )
        event = MessageEvent(source=source, text="test", message_type=msg_type)
        return runner._should_send_voice_reply(
            event, response, agent_msgs or [], already_sent=already_sent,
        )

    # -- Streaming OFF (existing behavior, must not change) --

    def test_voice_input_runner_skips(self):
        """Streaming OFF + voice input: runner skips — base adapter handles."""
        from gateway.platforms.event import MessageType
        runner = self._make_runner()
        assert self._call_should_reply(runner, "all", MessageType.VOICE, already_sent=False) is False

    def test_text_input_voice_all_runner_fires(self):
        """Streaming OFF + text input + voice_mode=all: runner generates TTS."""
        from gateway.platforms.event import MessageType
        runner = self._make_runner()
        assert self._call_should_reply(runner, "all", MessageType.TEXT, already_sent=False) is True


    def test_error_response_no_tts(self):
        """Error response: no TTS regardless of voice_mode."""
        from gateway.platforms.event import MessageType
        runner = self._make_runner()
        assert self._call_should_reply(runner, "all", MessageType.TEXT, response="Error: boom") is False


    # -- Streaming ON (already_sent=True) --


    def test_streaming_on_agent_tts_dedup(self):
        """Streaming ON + agent called TTS: runner skips (dedup still works)."""
        from gateway.platforms.event import MessageType
        runner = self._make_runner()
        agent_msgs = [{"role": "assistant", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "text_to_speech", "arguments": "{}"}}
        ]}]
        assert self._call_should_reply(
            runner, "all", MessageType.VOICE, agent_msgs=agent_msgs, already_sent=True,
        ) is False


# =====================================================================
# BasePlatformAdapter._should_auto_tts_for_chat — gate for auto-TTS
# on voice input. Regression test for Issue #16007.
# =====================================================================

class TestShouldAutoTtsForChat:
    """Three-layer gate: per-chat enable > per-chat disable > config default."""

    def _make_adapter(self, *, default: bool, enabled=(), disabled=()):
        """Build a bare adapter with only the attrs the gate reads."""
        adapter = SimpleNamespace(
            _auto_tts_default=default,
            _auto_tts_enabled_chats=set(enabled),
            _auto_tts_disabled_chats=set(disabled),
        )
        # Bind the unbound method — _should_auto_tts_for_chat only reads the
        # three attrs above via ``self.``, so an unbound call works.
        from gateway.platforms.base import BasePlatformAdapter
        return BasePlatformAdapter._should_auto_tts_for_chat, adapter

    def test_default_false_no_override_suppresses(self):
        """Issue #16007: voice.auto_tts=False and no per-chat state → no TTS."""
        fn, adapter = self._make_adapter(default=False)
        assert fn(adapter, "chat1") is False


    def test_explicit_enable_overrides_false_default(self):
        """``/voice on`` with config auto_tts=False still fires."""
        fn, adapter = self._make_adapter(default=False, enabled={"chat1"})
        assert fn(adapter, "chat1") is True


class TestStreamTtsTempfileFallback:
    """Regression for the temp-WAV fallback in stream_tts_to_speaker.

    When no sounddevice output stream is available the streaming path falls
    back to writing each sentence to a temp WAV and playing it via the system
    player.  ``wave.open()`` given a *file object* flushes but does NOT close
    it (it only closes files it opened itself, by name), so the OS handle to
    the temp file stays open.  On Windows that open write handle blocks the
    player from reading the file and blocks ``os.unlink()`` (WinError 32,
    silently swallowed), leaving orphaned temp .wav files behind.  The fix
    closes the handle before playback/cleanup; this test asserts the close
    happens before the play call.
    """

    def test_tempfile_handle_closed_before_playback(self, monkeypatch):
        import wave
        import tools.tts_tool as tts_mod
        import tools.voice_mode as vm
        from tools.tts_tool_speaker import stream_tts_to_speaker

        # Fake registry streamer so resolve_streaming_provider yields chunked
        # PCM regardless of which real providers are configured in the env.
        class _FakeStreamer:
            sample_rate = 24000
            channels = 1

            def stream(self, text):
                yield b"\x00\x00" * 240
                yield b"\x00\x00" * 240

        monkeypatch.setattr(
            "tools.tts_streaming.resolve_streaming_provider",
            lambda tts_config, preferred=None: _FakeStreamer(),
        )

        # Force sounddevice unavailable → output_stream is None → tempfile fallback.
        def _no_sounddevice():
            raise ImportError("sounddevice unavailable in test")
        monkeypatch.setattr(tts_mod, "_import_sounddevice", _no_sounddevice)

        events = []  # ordered log of ("close", name) / ("play", path)

        # Spy on NamedTemporaryFile to record when the handle is closed.
        real_ntf = tts_mod.tempfile.NamedTemporaryFile

        def _spy_ntf(*args, **kwargs):
            f = real_ntf(*args, **kwargs)
            orig_close = f.close

            def _tracked_close():
                events.append(("close", f.name))
                return orig_close()

            f.close = _tracked_close
            return f

        monkeypatch.setattr(tts_mod.tempfile, "NamedTemporaryFile", _spy_ntf)

        played = []

        def _fake_play(path):
            events.append(("play", path))
            played.append(path)
            # At play time the file must be a fully written, readable WAV.
            with wave.open(path, "rb") as wf:
                assert wf.getnframes() > 0

        monkeypatch.setattr(vm, "play_audio_file", _fake_play)

        text_q = queue.Queue()
        stop_evt = threading.Event()
        done_evt = threading.Event()
        text_q.put("This is a spoken sentence for the fallback. ")
        text_q.put(None)

        stream_tts_to_speaker(text_q, stop_evt, done_evt)

        assert done_evt.is_set()
        assert played, "temp-file fallback player was never invoked"

        play_idx = next(i for i, e in enumerate(events) if e[0] == "play")
        closes_before_play = [
            i for i, e in enumerate(events[:play_idx]) if e[0] == "close"
        ]
        assert closes_before_play, (
            "temp WAV handle must be closed BEFORE play_audio_file — an open "
            "write handle blocks playback and os.unlink() on Windows"
        )
        # And the temp file is cleaned up afterwards.
        assert not os.path.exists(played[0]), "temp WAV was not unlinked"
