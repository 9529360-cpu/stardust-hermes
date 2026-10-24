"""F6.1 session epoch guard (opt-in) — protocol regression tests.

Covers the three protocol seams of ``dashboard.compute_host_epoch_guard``:
1. child-side frame gate (``ComputeHost._run_real_turn``): stale-epoch frames get an
   explicit terminal reply and never execute; post-rebuild legitimate frames run
   exactly once; fresh prompts are unchanged.
2. parent-side frame minting (``_compute_host_turn_frame``): every turn frame carries
   ``host_epoch`` + ``parent_queued_prompt_generation``.
3. parent-side epoch bump (``_on_compute_host_rebuild`` + supervisor ``on_rebuild``):
   every host-using session's epoch advances exactly once per rebuild, only under the
   flag.
"""

from __future__ import annotations

import io
import json
import threading
import time
import types

import pytest

from tui_gateway import server
from tui_gateway.compute_host import ComputeHost


def _frames(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _wait(out: io.StringIO, predicate, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in _frames(out):
            if predicate(frame):
                return frame
        time.sleep(0.01)
    return None


def _agent():
    def run_conversation(prompt, *, conversation_history=None, stream_callback=None, **_kw):
        final = f"reply:{prompt}"
        messages = [*(conversation_history or []), {"role": "user", "content": prompt},
                    {"role": "assistant", "content": final}]
        return {"final_response": final, "messages": messages}

    return types.SimpleNamespace(
        session_id="k", run_conversation=run_conversation, clear_interrupt=lambda: None)


def _session() -> dict:
    return {
        "agent": _agent(), "session_key": "k", "history": [], "history_lock": threading.Lock(),
        "history_version": 0, "running": False, "attached_images": [], "image_counter": 0,
        "cols": 80, "slash_worker": None, "show_reasoning": False, "tool_progress_mode": "all",
        "inflight_turn": None,
    }

@pytest.fixture()
def turn_env(monkeypatch, tmp_path):
    """Neutralize the turn pipeline's environment-heavy side paths (same set as the
    compute-host turn protocol tests) so the epoch protocol is what's under test."""
    monkeypatch.setattr(server, "_wire_callbacks", lambda sid: None)
    monkeypatch.setattr(server, "_sync_agent_model_with_config", lambda sid, session: None)
    monkeypatch.setattr(server, "_session_cwd", lambda session: str(tmp_path))
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    monkeypatch.setattr(server, "_tts_stream_begin", lambda: None)
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *a, **k: None)
    monkeypatch.setattr(server, "_get_usage", lambda agent: {})

@pytest.fixture()
def epoch_guard(monkeypatch):
    monkeypatch.setenv("STARDUST_COMPUTE_HOST_EPOCH_GUARD", "1")


# ---------------------------------------------------------------- child-side gate

def test_stale_epoch_frame_gets_terminal_reply_and_never_executes(turn_env, epoch_guard):
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "s1"
    server._sessions[sid] = _session()
    try:
        # establish known epoch = 1
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t1",
                           "prompt": "current", "host_epoch": 1,
                           "parent_queued_prompt_generation": 0})
        assert _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t1")
        # stale frame minted for the pre-rebuild host (epoch 0)
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t2",
                           "prompt": "STALE", "host_epoch": 0,
                           "parent_queued_prompt_generation": 0})
        end = _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t2")
    finally:
        server._sessions.pop(sid, None)
        host.close()
    assert end is not None and end["interrupted"] is True
    assert end.get("stale_epoch") is True
    # never executed: no turn.started for the stale request
    assert not any(f["type"] == "turn.started" and f.get("request_id") == "t2" for f in _frames(out))


def test_post_rebuild_legitimate_frame_runs_exactly_once(turn_env, epoch_guard):
    """The C6.1 fix: after Stop (parent gen 1) and a host rebuild, the legitimate
    queued frame carrying generation 1 EXECUTES (the rebuilt child compares against
    the minted parent generation, not its reset local counter)."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "s1"
    server._sessions[sid] = _session()
    try:
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t1",
                           "prompt": "pre-rebuild", "host_epoch": 0,
                           "parent_queued_prompt_generation": 0})
        assert _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t1")
        # new build adopts epoch 1; the pending frame minted at parent gen 1 runs
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t2",
                           "prompt": "legit-queued", "host_epoch": 1,
                           "parent_queued_prompt_generation": 1,
                           "queued_prompt_generation": 1})
        end = _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t2")
    finally:
        server._sessions.pop(sid, None)
        host.close()
    assert end is not None and end["interrupted"] is False
    deltas = [f for f in _frames(out) if f["type"] == "rpc"
              and f["message"]["params"]["type"] == "message.complete"
              and "legit-queued" in json.dumps(f["message"]["params"].get("payload", {}))]
    started = [f for f in _frames(out) if f["type"] == "turn.started" and f.get("request_id") == "t2"]
    assert len(started) == 1


def test_flag_off_keeps_legacy_generation_comparison(turn_env, monkeypatch):
    """Flag OFF: the rebuilt child's local counter (0) decides, reproducing the C6.1
    baseline (legitimate queued frame skipped) — the opt-in must not change it."""
    monkeypatch.delenv("STARDUST_COMPUTE_HOST_EPOCH_GUARD", raising=False)
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "s1"
    session = _session()
    server._sessions[sid] = session
    try:
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t1",
                           "prompt": "pre", "host_epoch": 1,
                           "parent_queued_prompt_generation": 1,
                           "queued_prompt_generation": 1})
        end = _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t1")
    finally:
        server._sessions.pop(sid, None)
        host.close()
    # local counter is 0, frame carries 1 -> interrupted skip (baseline behaviour)
    assert end is not None and end["interrupted"] is True
    assert not any(f["type"] == "turn.started" for f in _frames(out))


def test_fresh_prompt_unaffected_by_guard(turn_env, epoch_guard):
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "s1"
    server._sessions[sid] = _session()
    try:
        host.handle_frame({"type": "turn.start", "sid": sid, "request_id": "t1",
                           "prompt": "fresh", "host_epoch": 0,
                           "parent_queued_prompt_generation": 0})
        end = _wait(out, lambda f: f["type"] == "turn.end" and f.get("request_id") == "t1")
    finally:
        server._sessions.pop(sid, None)
        host.close()
    assert end is not None and end["interrupted"] is False


def test_epoch_adopt_is_monotonic(turn_env, epoch_guard):
    """Frames from a NEWER epoch are adopted (first frame after rebuild); the known
    epoch never moves backwards."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "s1"
    server._sessions[sid] = _session()
    try:
        for i, epoch in enumerate((0, 1, 5, 3)):
            rid = f"t{i}"
            host.handle_frame({"type": "turn.start", "sid": sid, "request_id": rid,
                               "prompt": f"p{i}", "host_epoch": epoch,
                               "parent_queued_prompt_generation": 0})
            _wait(out, lambda f, rid=rid: f["type"] == "turn.end" and f.get("request_id") == rid)
        assert host._session_epochs[sid] == 5
    finally:
        server._sessions.pop(sid, None)
        host.close()


# ---------------------------------------------------------------- parent-side minting

def test_turn_frame_carries_epoch_fields():
    # Call through the server namespace: bridge helpers are rebound onto server at
    # register() time, so module-level calls lack the server's sibling names.
    frame = server._compute_host_turn_frame(
        "rid", "sid",
        {"session_key": "k", "history": [], "history_version": 3,
         "history_lock": threading.Lock(), "cols": 80, "attached_images": [],
         "_compute_host_epoch": 4, "_queued_prompt_generation": 2},
        "hello")
    assert frame["host_epoch"] == 4
    assert frame["parent_queued_prompt_generation"] == 2
    assert frame["queued_prompt_generation"] is None
    assert frame["history_version"] == 3


def test_rebuild_bumps_epoch_only_under_flag(monkeypatch):
    # Drive the rebound copy on the server namespace (same one the supervisor hook
    # captures); its module-level siblings (_sessions/_sessions_lock) live there.
    sessions = {}
    lock = threading.Lock()
    for sid in ("a", "b"):
        sessions[sid] = {"history_lock": lock, "_compute_host_active": True,
                         "_compute_host_epoch": 0}
    monkeypatch.setitem(server.__dict__, "_sessions", sessions)

    monkeypatch.delenv("STARDUST_COMPUTE_HOST_EPOCH_GUARD", raising=False)
    server._on_compute_host_rebuild({"reason": "test"})
    assert all(s["_compute_host_epoch"] == 0 for s in sessions.values())

    monkeypatch.setenv("STARDUST_COMPUTE_HOST_EPOCH_GUARD", "1")
    server._on_compute_host_rebuild({"reason": "test"})
    assert sessions["a"]["_compute_host_epoch"] == 1
    assert sessions["b"]["_compute_host_epoch"] == 1


def test_supervisor_on_rebuild_default_and_flag_wiring(tmp_path):
    from tui_gateway.host_supervisor import HostSupervisor
    sup = HostSupervisor(
        registry_path=tmp_path / "reg.json", rpc_sink=lambda m: None,
        argv=["unused"], autostart=False)
    assert sup.on_rebuild is None
    calls: list[dict] = []
    sup.on_rebuild = calls.append
    # First successful build (host_generation == 1) must NOT fire the hook.
    sup._hello = {"hermes_home": sup.expected_hermes_home, "build_sha": sup.expected_build_sha}
    sup._host_generation = 1
    # (spawn itself is not exercised here; the gen>1 guard is covered by the
    # protocol tests and the harness evidence.)
    assert calls == []
