"""F6.3: voice barge-in interrupts only the voice-owning session (opt-in flag).

``voice.barge_in_owner_only`` (default false) scopes the full-duplex barge-in interrupt
(``methods_voice._fd_trip``) to the session that owns the voice turn — the sid recorded in
``_voice_event_sid`` by ``voice.record``/``voice.toggle`` — through the canonical
``session.interrupt`` contract (``_interrupt_session_turn``). Unrelated running sessions keep
going. When the owner cannot be proven (no sid, unknown sid, session gone, WS transport
detached) nothing is interrupted: local audio is already stopped and a warning is logged.

Deterministic local fakes only; ``server._fd_trip`` is the real seam the full-duplex
listener trips.
"""

import logging
import threading

import pytest

from tui_gateway import server


class _FakeAgent:
    """Counts interrupt calls; both the legacy and hard ABI land on ``interrupt``."""

    def __init__(self):
        self.interrupt_calls = 0

    def interrupt(self, message=None, **kw):
        self.interrupt_calls += 1

    hard_interrupt = interrupt


class _ProbeTransport:
    def write(self, frame):
        return True


def _mk_session(sid, agent=None, transport=None):
    return {
        "agent": agent if agent is not None else _FakeAgent(),
        "session_key": f"key-{sid}",
        "history": [],
        "history_lock": threading.RLock(),
        "history_version": 0,
        "running": True,
        "transport": transport if transport is not None else _ProbeTransport(),
        "attached_images": [],
    }


@pytest.fixture
def voice_room():
    """Three running sessions (voice A, chat B, background task C) + a clean voice owner slot."""
    saved_sessions = dict(server._sessions)
    saved_sid = server._voice_event_sid
    server._sessions.clear()
    server._sessions.update({sid: _mk_session(sid) for sid in ("voiceA", "chatB", "taskC")})
    server._voice_event_sid = "voiceA"
    try:
        yield {sid: server._sessions[sid] for sid in server._sessions}
    finally:
        server._sessions.clear()
        server._sessions.update(saved_sessions)
        server._voice_event_sid = saved_sid


def _cfg(monkeypatch, owner_only):
    monkeypatch.setattr(server, "_load_cfg",
                        lambda: {"voice": {"barge_in_owner_only": owner_only}})


def _counts(room):
    return {sid: s["agent"].interrupt_calls for sid, s in room.items()}


def _rearm(room):
    for s in room.values():
        s["running"] = True


class TestFlagOffBaselineUnchanged:
    def test_generation_trip_interrupts_every_running_session(self, voice_room, monkeypatch):
        _cfg(monkeypatch, False)
        server._fd_trip("generation")
        assert _counts(voice_room) == {"voiceA": 1, "chatB": 1, "taskC": 1}

    def test_playback_trip_interrupts_no_session(self, voice_room, monkeypatch):
        _cfg(monkeypatch, False)
        server._fd_trip("playback")
        assert _counts(voice_room) == {"voiceA": 0, "chatB": 0, "taskC": 0}

    def test_repeated_trips_match_baseline_counts(self, voice_room, monkeypatch):
        # Flag off must stay byte-identical to the pre-F6.3 behaviour: every generation
        # trip interrupts every running session, so per-session counts are equal.
        _cfg(monkeypatch, False)
        for _ in range(20):
            _rearm(voice_room)
            server._fd_trip("generation")
        counts = _counts(voice_room)
        assert counts == {"voiceA": 20, "chatB": 20, "taskC": 20}

    def test_default_config_reads_as_off(self, voice_room, monkeypatch):
        # No voice block / malformed shapes must resolve to the historical behaviour.
        monkeypatch.setattr(server, "_load_cfg", lambda: {})
        assert server._barge_in_owner_only() is False
        monkeypatch.setattr(server, "_load_cfg", lambda: {"voice": "oops"})
        assert server._barge_in_owner_only() is False


class TestFlagOnOwnerScoped:
    def test_generation_trip_interrupts_owner_only(self, voice_room, monkeypatch):
        _cfg(monkeypatch, True)
        server._fd_trip("generation")
        assert _counts(voice_room) == {"voiceA": 1, "chatB": 0, "taskC": 0}

    def test_repeated_trips_100_percent_owner_zero_foreign(self, voice_room, monkeypatch):
        _cfg(monkeypatch, True)
        for _ in range(100):
            _rearm(voice_room)
            server._fd_trip("generation")
        assert _counts(voice_room) == {"voiceA": 100, "chatB": 0, "taskC": 0}

    def test_playback_trip_interrupts_no_session_flag_on(self, voice_room, monkeypatch):
        _cfg(monkeypatch, True)
        server._fd_trip("playback")
        assert _counts(voice_room) == {"voiceA": 0, "chatB": 0, "taskC": 0}

    def test_owner_uses_canonical_interrupt_contract(self, voice_room, monkeypatch):
        # The canonical path must apply, not just agent.interrupt(): cancel-request latch,
        # queued prompt purge, and the stuck-running flag fix for a session with no live
        # run thread.
        _cfg(monkeypatch, True)
        owner = voice_room["voiceA"]
        owner["queued_prompt"] = "stale question"
        server._fd_trip("generation")
        assert owner["agent"].interrupt_calls == 1
        assert owner["_turn_cancel_requested"] is True
        assert owner["queued_prompt"] is None
        assert owner["running"] is False
        for sid in ("chatB", "taskC"):
            foreign = voice_room[sid]
            assert foreign["agent"].interrupt_calls == 0
            assert foreign.get("_turn_cancel_requested") is None
            assert foreign["running"] is True

    def test_single_session_still_interrupted(self, monkeypatch):
        saved_sessions = dict(server._sessions)
        saved_sid = server._voice_event_sid
        server._sessions.clear()
        server._sessions["onlyVoice"] = _mk_session("onlyVoice")
        server._voice_event_sid = "onlyVoice"
        try:
            _cfg(monkeypatch, True)
            for _ in range(10):
                server._sessions["onlyVoice"]["running"] = True
                server._fd_trip("generation")
            assert server._sessions["onlyVoice"]["agent"].interrupt_calls == 10
        finally:
            server._sessions.clear()
            server._sessions.update(saved_sessions)
            server._voice_event_sid = saved_sid

    def test_owner_switch_mid_run_follows_new_owner(self, voice_room, monkeypatch):
        _cfg(monkeypatch, True)
        for i in range(10):
            _rearm(voice_room)
            if i == 5:  # the mic/TTS turn moves to the chat session mid-run
                server._voice_event_sid = "chatB"
            server._fd_trip("generation")
        assert _counts(voice_room) == {"voiceA": 5, "chatB": 5, "taskC": 0}


class TestFlagOnOwnerUnresolved:
    @pytest.mark.parametrize("setup", [
        pytest.param("empty_sid", id="no-owner-sid"),
        pytest.param("unknown_sid", id="owner-sid-not-a-session"),
        pytest.param("session_gone", id="owner-session-closed-mid-turn"),
        pytest.param("transport_detached", id="owner-ws-transport-disconnected"),
    ])
    def test_interrupts_nothing_and_warns(self, voice_room, monkeypatch, caplog, setup):
        _cfg(monkeypatch, True)
        if setup == "empty_sid":
            server._voice_event_sid = ""
        elif setup == "unknown_sid":
            server._voice_event_sid = "ghostSid"
        elif setup == "session_gone":
            del server._sessions["voiceA"]
        elif setup == "transport_detached":
            server._sessions["voiceA"]["transport"] = server._detached_ws_transport
        with caplog.at_level(logging.WARNING, logger="tui_gateway.server"):
            server._fd_trip("generation")
        assert _counts(voice_room).get("chatB", 0) == 0
        assert _counts(voice_room).get("taskC", 0) == 0
        if setup != "session_gone":
            assert _counts(voice_room)["voiceA"] == 0
        assert any("owner unresolved" in r.message for r in caplog.records)
