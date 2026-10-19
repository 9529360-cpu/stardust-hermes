"""A completed goal turn survives the handoff gap, without undoing Stop or newer work."""

from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from hermes_cli import goals
from hermes_state_registry import acquire, release_or_close
from tui_gateway import server
from tui_gateway.turn_marker import read_turn_marker, record_turn_start


def _session(home, db):
    return {
        "session_key": "goal-session", "profile_home": str(home), "source": "tui",
        "agent": SimpleNamespace(interrupt=lambda: None), "running": False,
        "history": db.get_messages("goal-session"), "history_lock": threading.Lock(),
        "attached_images": [], "inflight_turn": None,
    }


@pytest.fixture()
def recovery(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("STARDUST_HOME", str(home))
    monkeypatch.setattr(server, "_hermes_home", home)
    db = acquire(home / "state.db")
    db.create_session("goal-session", "tui")
    db.append_message("goal-session", "user", "Repair the artifact")
    message_id = db.append_message("goal-session", "assistant", "First attempt finished")
    monkeypatch.setattr(goals, "judge_goal", lambda *a, **k: ("continue", "Needs work", False, None, False))
    manager = goals.GoalManager("goal-session", session_db=db)
    manager.set("Repair the artifact", max_turns=5,
                contract=goals.GoalContract(constraints="Keep guard.txt unchanged"))
    assert manager.evaluate_after_turn("First attempt finished", completed_message_id=message_id)["should_continue"]

    jobs, calls, events = [], [], []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            jobs.append(self.target)

    # The DB and recovery decisions are real; only dispatch timing and the model boundary are controlled.
    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(server, "_start_agent_build", lambda *a: None)
    monkeypatch.setattr(server, "_wait_agent", lambda *a, **k: None)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *a: None)
    original_submit = server._run_prompt_submit
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(server, "_emit", lambda event, *a: events.append(event))
    config = {"desktop": {"auto_continue": {"enabled": True, "max_attempts": 2}}}
    monkeypatch.setattr(server, "_load_cfg", lambda: config)
    ctx = SimpleNamespace(home=home, db=db, jobs=jobs, calls=calls, events=events, config=config,
                          session=_session(home, db), original_submit=original_submit)
    ctx.manager = lambda: goals.GoalManager("goal-session", session_db=db)
    yield ctx
    release_or_close(db)


def _schedule(ctx, session=None):
    return server._maybe_schedule_auto_continue("runtime", session or ctx.session, "goal-session")


def _interrupt_env(monkeypatch, ctx):
    monkeypatch.setattr(server, "_tts_stream_stop", lambda: None)
    monkeypatch.setattr(server, "_clear_pending", lambda *a: None)
    monkeypatch.setattr(server, "_sess_nowait", lambda *a: (ctx.session, None))
    monkeypatch.setattr(server, "_sess", lambda *a: (ctx.session, None))
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: False)


def _stop():
    return server._methods["session.interrupt"]("stop", {"session_id": "runtime"})


def test_no_marker_recovers_once_and_preserves_goal_budget(recovery):
    ctx = recovery
    original = ctx.manager().state
    assert read_turn_marker(ctx.home, "goal-session") is None
    assert _schedule(ctx)["attempt"] == 1
    ctx.jobs.pop()()
    assert len(ctx.calls) == 1
    args, kwargs = ctx.calls[0]
    assert "Repair the artifact" in args[3]
    assert "Keep guard.txt unchanged" in args[3]
    assert kwargs["display_kind"] == "auto_continue"
    state = ctx.manager().state
    assert (state.turns_used, state.max_turns, state.created_at) == (1, 5, original.created_at)
    assert state.continuation_attempts == 1
    assert _schedule(ctx) is None
    assert not ctx.jobs
    server._record_turn_marker(ctx.session, args[3])
    assert read_turn_marker(ctx.home, "goal-session")["attempts"] == 1


@pytest.mark.parametrize("change", ["pause", "new_message"])
def test_goal_changed_during_deferred_build_is_not_dispatched(recovery, monkeypatch, change):
    ctx = recovery

    def wait_agent(*args, **kwargs):
        if change == "pause":
            ctx.manager().pause()
        else:
            ctx.db.append_message("goal-session", "user", "A newer instruction")

    monkeypatch.setattr(server, "_wait_agent", wait_agent)
    assert _schedule(ctx)
    ctx.jobs.pop()()
    assert not ctx.calls and "message.start" not in ctx.events
    assert ctx.manager().state.continuation_attempts == 0
    assert ctx.manager().state.turns_used == 1
    assert ctx.session["running"] is False
    assert _schedule(ctx, _session(ctx.home, ctx.db)) is None


def test_ownership_refusal_precedes_durable_claim(recovery, monkeypatch):
    ctx = recovery
    observed = []

    def refuse(*args):
        observed.append(ctx.manager().state.continuation_attempts)
        return "another backend owns this session"

    monkeypatch.setattr(server, "_ensure_active_session_slot", refuse)
    assert _schedule(ctx)
    ctx.jobs.pop()()
    assert observed == [0]
    assert ctx.manager().state.continuation_attempts == 0
    assert not ctx.calls and "message.start" not in ctx.events
    assert ctx.session["running"] is False


def test_disabled_auto_continue_does_not_claim_goal(recovery):
    ctx = recovery
    ctx.config["desktop"]["auto_continue"]["enabled"] = False
    assert _schedule(ctx) is None
    assert not ctx.jobs and not ctx.calls
    assert ctx.manager().state.continuation_attempts == 0


def test_exhausted_live_marker_cannot_reopen_old_goal_retry_budget(recovery):
    ctx = recovery
    record_turn_start(ctx.home, "goal-session", "recovered turn", attempts=2)
    assert _schedule(ctx) is None
    assert read_turn_marker(ctx.home, "goal-session") is None
    state = ctx.manager().state
    assert state.status == "active" and state.turns_used == 1
    assert state.continuation_after_message_id == 0
    assert _schedule(ctx, _session(ctx.home, ctx.db)) is None
    assert not ctx.jobs and not ctx.calls


@pytest.mark.parametrize("replacement", [False, True])
def test_confirmed_stop_survives_ram_loss_and_preserves_replacement(recovery, monkeypatch, replacement):
    ctx = recovery
    old_goal = ctx.manager().state
    _interrupt_env(monkeypatch, ctx)
    ctx.session["running"] = True
    record_turn_start(ctx.home, "goal-session", "stopped turn")

    def acknowledge(*args, **kwargs):
        if replacement:
            ctx.manager().set("New independent goal", max_turns=7)
        return {"type": "interrupt.ack", "applied": True}

    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a: True)
    monkeypatch.setattr(server, "_get_compute_host_supervisor", lambda: SimpleNamespace(interrupt=acknowledge))
    assert _stop()["result"]["status"] == "interrupted"
    assert read_turn_marker(ctx.home, "goal-session") is None
    state = ctx.manager().state
    assert state.status == ("active" if replacement else "paused")
    assert state.goal == ("New independent goal" if replacement else old_goal.goal)
    assert state.continuation_after_message_id == 0
    if replacement:
        server._goal_followup_after_turn(
            "runtime", ctx.session, {}, "interrupted", "", interrupted_goal=old_goal)
        assert ctx.manager().state.status == "active"
        assert ctx.manager().state.max_turns == 7
    ctx.session = _session(ctx.home, ctx.db)
    assert _schedule(ctx) is None
    assert not ctx.jobs and not ctx.calls


def test_stop_during_message_start_blocks_real_turn_admission(recovery, monkeypatch):
    ctx = recovery
    _interrupt_env(monkeypatch, ctx)
    admitted = []

    def emit(event, *args):
        if event == "message.start":
            assert _stop()["result"]["status"] == "interrupted"

    def submit(*args, **kwargs):
        admitted.append(ctx.original_submit(*args, **kwargs))

    monkeypatch.setattr(server, "_emit", emit)
    monkeypatch.setattr(server, "_run_prompt_submit", submit)
    assert _schedule(ctx)
    ctx.jobs.pop()()
    assert admitted == [False]
    assert ctx.session["running"] is False
    assert ctx.manager().state.status == "paused"
    assert read_turn_marker(ctx.home, "goal-session") is None
    # A refused recovery must not lend its stopped goal prompt/retry count to
    # the next independently authorized user turn, even if that turn crashes.
    ctx.session.pop("_turn_cancel_requested", None)
    server._record_turn_marker(ctx.session, "A new manual request")
    marker = read_turn_marker(ctx.home, "goal-session")
    assert marker["prompt"] == "A new manual request"
    assert marker["attempts"] == 0


def test_warm_goal_followup_from_before_stop_is_not_dispatched(recovery, monkeypatch):
    ctx = recovery
    _interrupt_env(monkeypatch, ctx)
    generation = int(ctx.session.get("_queued_prompt_generation", 0))
    assert _stop()["result"]["status"] == "interrupted"
    server._run_post_turn_followups(
        "finished-turn", "runtime", ctx.session, {}, "Continue the old goal",
        queued_prompt_generation=generation)
    assert not ctx.calls and "message.start" not in ctx.events
    assert ctx.session["running"] is False
    assert ctx.manager().state.status == "paused"


def test_goal_snapshot_read_failure_stops_turn_but_cannot_ack_durable_stop(recovery, monkeypatch):
    ctx = recovery
    _interrupt_env(monkeypatch, ctx)
    ctx.session["running"] = True
    interrupted, failed_keys = [], []
    ctx.session["agent"].interrupt = lambda: interrupted.append(True)
    record_turn_start(ctx.home, "goal-session", "turn to stop")
    original_get_meta = ctx.db.get_meta

    def fail_goal_read_once(key):
        if key == "goal:goal-session" and not failed_keys:
            failed_keys.append(key)
            raise sqlite3.OperationalError("injected goal snapshot read failure")
        return original_get_meta(key)

    monkeypatch.setattr(ctx.db, "get_meta", fail_goal_read_once)
    response = _stop()
    assert failed_keys == ["goal:goal-session"]
    assert interrupted == [True]
    assert ctx.session["_turn_cancel_requested"] is True
    assert ctx.session["running"] is False
    assert response["error"]["code"] == 5019 and "result" not in response
    assert "goal pause could not be persisted" in response["error"]["message"]
    assert read_turn_marker(ctx.home, "goal-session") is None
    assert ctx.manager().state.status == "active"
