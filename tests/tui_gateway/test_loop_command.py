"""Tests for /loop handling in tui_gateway.

The TUI routes ``/loop`` through ``command.dispatch`` (same rationale as
``/goal`` — the CLI handler drives ``_pending_input``, which the slash
worker has no reader for). State mutations go through the shared
``dispatch_loop_command``; the per-session notification poller fires due
wakeups via ``_maybe_fire_tui_loop_tick``.
"""

from __future__ import annotations

import importlib
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        __import__("tui_gateway.server_requests", fromlist=["x"]).reset_for_tests()


@pytest.fixture()
def session(server):
    sid = "sid-loop-test"
    session_key = "tui-loop-session-1"
    s = {
        "session_key": session_key,
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "cols": 120,
    }
    server._sessions[sid] = s
    return sid, session_key, s


def _call(server, method, **params):
    handler = server._methods[method]
    return handler(1, params)


# ── command.dispatch /loop ────────────────────────────────────────────


def test_loop_bare_shows_status_when_none_set(server, session):
    sid, _, _ = session
    r = _call(server, "command.dispatch", name="loop", arg="", session_id=sid)
    assert r["result"]["type"] == "exec"
    assert "No loop set" in r["result"]["output"]


def test_loop_set_persists(server, session):
    sid, session_key, _ = session
    r = _call(server, "command.dispatch", name="loop", arg="5m check the deploy", session_id=sid)
    result = r["result"]
    assert result["type"] == "exec"
    assert "Loop set" in result["output"]

    from hermes_cli.loops import LoopManager

    mgr = LoopManager(session_key)
    assert mgr.state is not None
    assert mgr.state.prompt == "check the deploy"
    assert mgr.state.status == "active"
    assert mgr.state.interval_seconds == 300.0


def test_loop_proactive_alias_resolves(server, session):
    sid, _, _ = session
    r = _call(server, "command.dispatch", name="proactive", arg="5m ping", session_id=sid)
    assert "Loop set" in r["result"]["output"]


def test_loop_pause_resume_stop(server, session):
    sid, session_key, _ = session
    _call(server, "command.dispatch", name="loop", arg="5m poll CI", session_id=sid)

    r = _call(server, "command.dispatch", name="loop", arg="pause", session_id=sid)
    assert "paused" in r["result"]["output"].lower()

    r = _call(server, "command.dispatch", name="loop", arg="resume", session_id=sid)
    assert "resumed" in r["result"]["output"].lower()

    r = _call(server, "command.dispatch", name="loop", arg="stop", session_id=sid)
    assert "stopped" in r["result"]["output"].lower()

    from hermes_cli.loops import LoopManager

    assert not LoopManager(session_key).has_loop()


def test_loop_requires_session(server):
    r = _call(server, "command.dispatch", name="loop", arg="5m x", session_id="unknown")
    assert "error" in r
    assert r["error"]["code"] == 4001


# ── idle wakeup driver ────────────────────────────────────────────────


def test_tui_tick_fires_when_idle_and_due(server, session):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager, save_loop

    mgr = LoopManager(session_key)
    mgr.set("poll the build", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)

    fired = {}

    def fake_submit(rid, sid_, session_, text, **kwargs):
        fired["text"] = text
        return True

    with patch.object(server, "_run_prompt_submit", fake_submit), \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    assert "poll the build" in fired.get("text", "")
    assert "[/loop wakeup #1" in fired["text"]
    # Session claimed for the wakeup turn.
    assert s["running"] is True


def test_tui_tick_abandons_claim_when_prompt_turn_refuses_to_start(server, session):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager, save_loop

    mgr = LoopManager(session_key)
    mgr.set("poll the build", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)

    with patch.object(server, "_run_prompt_submit", return_value=False), \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    state = LoopManager(session_key).state
    assert state.awaiting_response is False
    assert state.ticks_fired == 0
    assert s["running"] is False


def test_tui_slash_tick_abandons_claim_when_send_turn_refuses_to_start(
    server, session, monkeypatch
):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager, save_loop

    mgr = LoopManager(session_key)
    mgr.set("/work check the build", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)
    monkeypatch.setitem(
        server._methods,
        "command.dispatch",
        lambda rid, params: {"id": rid, "result": {"type": "send", "message": "expanded work"}},
    )

    with patch.object(server, "_run_prompt_submit", return_value=False), \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    state = LoopManager(session_key).state
    assert state.awaiting_response is False
    assert state.ticks_fired == 0
    assert s["running"] is False


def test_tui_slash_tick_abandons_claim_when_send_turn_raises(
    server, session, monkeypatch
):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager, save_loop

    mgr = LoopManager(session_key)
    mgr.set("/work check the build", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)
    monkeypatch.setitem(
        server._methods,
        "command.dispatch",
        lambda rid, params: {"id": rid, "result": {"type": "send", "message": "expanded work"}},
    )

    with patch.object(server, "_run_prompt_submit", side_effect=RuntimeError("dispatch exploded")), \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    state = LoopManager(session_key).state
    assert state.awaiting_response is False
    assert state.ticks_fired == 0
    assert s["running"] is False


def test_tui_tick_defers_when_running(server, session):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager, save_loop

    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)
    s["running"] = True

    with patch.object(server, "_run_prompt_submit") as submit, \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    submit.assert_not_called()
    # Tick not consumed — still due for the next poll.
    assert LoopManager(session_key).state.ticks_fired == 0


def test_tui_tick_defers_to_active_goal(server, session):
    sid, session_key, s = session
    from hermes_cli.goals import GoalManager
    from hermes_cli.loops import LoopManager, save_loop

    GoalManager(session_id=session_key).set("finish the feature")
    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=60)
    mgr.state.next_due_at = time.time() - 1
    save_loop(session_key, mgr.state)

    with patch.object(server, "_run_prompt_submit") as submit, \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    submit.assert_not_called()
    assert s["running"] is False


def test_tui_tick_noop_when_not_due(server, session):
    sid, session_key, s = session
    from hermes_cli.loops import LoopManager

    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=300)
    # New loops are due immediately; push the wakeup out to model "not due".
    from hermes_cli.loops import save_loop
    mgr.state.next_due_at = time.time() + 300
    save_loop(session_key, mgr.state)

    with patch.object(server, "_run_prompt_submit") as submit, \
         patch.object(server, "_emit"):
        server._maybe_fire_tui_loop_tick(sid, s)

    submit.assert_not_called()
    assert s["running"] is False


def _run_claimed_loop_turn(server, monkeypatch, tmp_path, session, result):
    """Run the real prompt-turn finalizer for an already-fired loop wakeup."""
    from types import SimpleNamespace

    sid, session_key, s = session

    class _InlineThread:
        def __init__(self, target=None, daemon=None, args=(), kwargs=None):
            self._target, self._args, self._kwargs = target, args, kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    for name, value in {
        "_wire_callbacks": lambda _sid: None,
        "_sync_agent_model_with_config": lambda _sid, _session: None,
        "_session_cwd": lambda _session: str(tmp_path),
        "_register_session_cwd": lambda _session: None,
        "_tts_stream_begin": lambda: None,
        "_sync_session_key_after_compress": lambda *args, **kwargs: None,
        "_get_usage": lambda _agent: {},
        "_emit_settled_session_info": lambda *_args, **_kwargs: None,
        "_hermes_home": tmp_path,
    }.items():
        monkeypatch.setattr(server, name, value)
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)
    def run_conversation(*_args, **_kwargs):
        if isinstance(result, BaseException):
            raise result
        return result

    s.update({
        "agent": SimpleNamespace(
            session_id=session_key,
            clear_interrupt=lambda: None,
            run_conversation=run_conversation,
        ),
        "image_counter": 0,
        "inflight_turn": None,
        "running": True,
        "show_reasoning": False,
        "slash_worker": None,
        "tool_progress_mode": "all",
    })
    assert server._run_prompt_submit("loop-rid", sid, s, "loop wakeup")


def test_interrupted_loop_wakeup_pauses_and_releases_awaiting_response(
    server, session, monkeypatch, tmp_path
):
    from hermes_cli.loops import LoopManager

    _sid, session_key, _s = session
    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=60)
    assert mgr.fire_tick()
    assert LoopManager(session_key).state.awaiting_response is True

    _run_claimed_loop_turn(
        server, monkeypatch, tmp_path, session,
        {"final_response": "", "interrupted": True},
    )

    state = LoopManager(session_key).state
    assert state.status == "paused"
    assert state.awaiting_response is False
    assert "interrupted" in (state.paused_reason or "")


def test_failed_loop_wakeup_releases_awaiting_response_and_keeps_loop_active(
    server, session, monkeypatch, tmp_path
):
    from hermes_cli.loops import LoopManager

    _sid, session_key, _s = session
    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=60)
    assert mgr.fire_tick()
    assert LoopManager(session_key).state.awaiting_response is True

    _run_claimed_loop_turn(
        server, monkeypatch, tmp_path, session,
        {"final_response": "", "error": "provider unavailable", "failed": True},
    )

    state = LoopManager(session_key).state
    assert state.status == "active"
    assert state.awaiting_response is False
    assert state.next_due_at > time.time()


def test_exception_during_loop_wakeup_releases_awaiting_response(
    server, session, monkeypatch, tmp_path
):
    from hermes_cli.loops import LoopManager

    _sid, session_key, _s = session
    mgr = LoopManager(session_key)
    mgr.set("poll", interval_seconds=60)
    assert mgr.fire_tick()
    assert LoopManager(session_key).state.awaiting_response is True

    _run_claimed_loop_turn(
        server, monkeypatch, tmp_path, session, RuntimeError("turn pipeline exploded")
    )

    state = LoopManager(session_key).state
    assert state.status == "active"
    assert state.awaiting_response is False
    assert state.next_due_at > time.time()
