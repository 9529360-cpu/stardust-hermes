import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tui_gateway import compute_host, server
from tui_gateway.compute_host import ComputeHost, _default_workers
from tui_gateway.host_supervisor import (
    MUTATOR_ROUTE_TABLE,
    HostSupervisor,
    append_log_record,
)


def _json_lines(out: io.StringIO) -> list[dict]:
    frames = []
    for line in out.getvalue().splitlines():
        if line.strip():
            frames.append(json.loads(line))
    return frames


def _wait_for_frame(out: io.StringIO, predicate, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in _json_lines(out):
            if predicate(frame):
                return frame
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for frame; saw={_json_lines(out)}")


def test_compute_host_workers_inherit_tui_pool_env_or_8(monkeypatch):
    monkeypatch.delenv("HERMES_TUI_RPC_POOL_WORKERS", raising=False)
    monkeypatch.delenv("HERMES_COMPUTE_HOST_WORKERS", raising=False)
    assert _default_workers() == 8

    monkeypatch.setenv("HERMES_TUI_RPC_POOL_WORKERS", "11")
    assert _default_workers() == 11

    # Dead-RC tombstone: malformed env falls back to 8, not the old except-branch 4.
    monkeypatch.setenv("HERMES_TUI_RPC_POOL_WORKERS", "not-an-int")
    assert _default_workers() == 8


def test_compute_host_routes_relayed_response_and_lock_to_its_open_request(monkeypatch):
    """The child owns the server request's wait: a relayed client response frame resolves it in-process,
    and a relayed ``clarify.lock`` is answered with that method's result for the parent to ack."""
    from tui_gateway import server_requests
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "host-clarify"
    server._sessions[sid] = {"history_lock": threading.Lock()}
    req = server_requests.ServerRequest(sid, "clarify", {"question": "?"})
    with server_requests._lock:
        server_requests._open[req.id] = req
    locks = []
    monkeypatch.setitem(server._methods, "clarify.lock",
                        lambda rid, params: locks.append((rid, dict(params))) or {"result": {"status": "ok", "remaining": []}})

    try:
        host._handle_respond({"sid": sid, "request_id": "relay-lock",
                              "params": {"lock": {"request_id": req.id, "question_id": "q0", "answer": "a"}}})
        assert locks == [("relay-lock", {"request_id": req.id, "question_id": "q0", "answer": "a"})]
        assert _json_lines(out)[-1]["response"] == {"result": {"status": "ok", "remaining": []}}

        host._handle_respond({"sid": sid, "request_id": "relay-response",
                              "params": {"frame": {"jsonrpc": "2.0", "id": req.id, "result": {"answer": "yes"}}}})
        assert req.answered and req.result == {"answer": "yes"} and req.event.is_set()
        frame = _json_lines(out)[-1]
        assert frame["type"] == "respond.ack" and frame["response"]["result"] == {"status": "ok"}
    finally:
        server._sessions.pop(sid, None)
        server_requests.reset_for_tests()
        host.close()


def test_compute_host_workspace_move_rehomes_running_runtime(monkeypatch, tmp_path):
    """A parent workspace move must update the child process used by later tools in the live turn."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "move-live"
    old_cwd = tmp_path / "old"
    new_cwd = tmp_path / "new"
    old_cwd.mkdir()
    new_cwd.mkdir()
    session = {
        "history_lock": threading.Lock(),
        "history": [],
        "history_version": 0,
        "session_key": "stored-key",
        "cwd": str(old_cwd),
        "running": True,
        "agent": object(),
    }
    registered = []
    server._sessions[sid] = session
    monkeypatch.setattr(
        server,
        "_register_session_cwd",
        lambda current: registered.append((current["session_key"], current["cwd"])),
    )
    monkeypatch.setattr(server, "_session_info", lambda _agent, _session=None: {})

    try:
        host.handle_frame({
            "type": "control",
            "sid": sid,
            "request_id": "move-1",
            "route_name": "session.workspace.move",
            "cwd": str(new_cwd),
        })
        frame = _json_lines(out)[-1]
    finally:
        server._sessions.pop(sid, None)
        host.close()

    assert frame["type"] == "control.ack"
    assert frame["request_id"] == "move-1"
    assert frame["result"] == {"cwd": str(new_cwd)}
    assert session["cwd"] == str(new_cwd)
    assert session["explicit_cwd"] is True
    assert session["cwd_from_settle"] is False
    assert registered == [("stored-key", str(new_cwd))]


def test_mutator_route_table_matches_prd_inventory():
    assert MUTATOR_ROUTE_TABLE == {
        "prompt.submit": "turn-path",
        "session.interrupt": "turn-path",
        "reload.mcp": "run-concurrent",
        "session.save": "run-concurrent",
        "session.workspace.move": "run-concurrent",
        "session.compress": "idle-gated",
        "prompt.submit.truncate": "idle-gated",
        "slash.model": "idle-gated",
        "slash.personality": "idle-gated",
        "slash.prompt": "idle-gated",
        "slash.compress": "idle-gated",
        "session.reset": "idle-gated",
        "session.history.reload": "idle-gated",
        "slash.retry": "idle-gated",
    }


def test_append_log_record_single_write_lines(tmp_path):
    path = tmp_path / "agent.log"

    def writer(i: int) -> None:
        append_log_record(path, f"line-{i:03d}-" + ("x" * 2000))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 32
    assert sorted(line.split("-", 2)[1] for line in lines) == [f"{i:03d}" for i in range(32)]
    assert all(line.endswith("x" * 2000) for line in lines)


def test_supervisor_drops_frames_from_a_superseded_host_generation(tmp_path):
    """Buffered stdout from a dead host must never satisfy the respawned host's waiters or hello gate."""
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        autostart=False,
    )
    supervisor._host_generation = 2
    supervisor._hello = {"boot_id": "new-boot"}
    supervisor._hello_event.clear()
    q = queue.Queue(maxsize=1)
    supervisor._pending_controls["reused-request"] = q

    supervisor._handle_host_frame(
        {"type": "control.ack", "request_id": "reused-request", "result": {"status": "old"}},
        source_generation=1,
    )
    supervisor._handle_host_frame(
        {"type": "hello", "boot_id": "old-boot"},
        source_generation=1,
    )

    assert q.empty()
    assert supervisor._hello == {"boot_id": "new-boot"}
    assert not supervisor._hello_event.is_set()

    current = {
        "type": "control.ack", "request_id": "reused-request", "result": {"status": "current"},
    }
    supervisor._handle_host_frame(current, source_generation=2)
    assert q.get_nowait() == current


def test_supervisor_drains_terminal_stdout_before_classifying_host_exit_as_crash(tmp_path, monkeypatch):
    """A terminal frame already written by the child must win over the wait-thread exit race."""
    completed = []
    emitted = []
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        rpc_sink=emitted.append,
        autostart=False,
    )
    supervisor._host_generation = 4
    supervisor._pending_turns["turn-1"] = ("sid-1", completed.append)

    class _ExitedProc:
        pid = 1234

        @staticmethod
        def wait():
            return 9

    proc = _ExitedProc()
    supervisor._proc = proc
    monkeypatch.setattr(supervisor, "_remove_registry", lambda: None)
    monkeypatch.setattr(supervisor, "_maybe_respawn_after_crash", lambda: None)

    class _StdoutDrain:
        def join(self, timeout=None):
            supervisor._handle_host_frame(
                {"type": "turn.end", "sid": "sid-1", "request_id": "turn-1"},
                source_generation=4,
            )

    supervisor._wait_for_exit(proc, stdout_thread=_StdoutDrain())

    assert completed == [{"type": "turn.end", "sid": "sid-1", "request_id": "turn-1"}]
    assert emitted == []
    assert supervisor._pending_turns == {}


def test_old_host_crash_cleanup_does_not_fail_replacement_host_work(tmp_path, monkeypatch):
    """Retire old pending work atomically before a replacement can register its own waiters."""
    completed = []
    emitted = []
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        rpc_sink=emitted.append,
        autostart=False,
    )
    old_control: queue.Queue[dict] = queue.Queue(maxsize=1)
    new_control: queue.Queue[dict] = queue.Queue(maxsize=1)
    supervisor._pending_turns["old-turn"] = ("old-sid", completed.append)
    supervisor._pending_controls["old-control"] = old_control

    class _ExitedProc:
        pid = 1234

        @staticmethod
        def wait():
            return 9

    class _StdoutDrain:
        @staticmethod
        def join(timeout=None):
            return None

    proc = _ExitedProc()
    supervisor._proc = proc

    def _replacement_arrives() -> None:
        # This hook runs after the crashed host has been retired from _proc. It models a
        # concurrent caller starting the replacement and registering new work before old
        # crash callbacks are delivered.
        supervisor._pending_turns["new-turn"] = ("new-sid", lambda _frame: None)
        supervisor._pending_controls["new-control"] = new_control

    monkeypatch.setattr(supervisor, "_remove_registry", _replacement_arrives)
    monkeypatch.setattr(supervisor, "_maybe_respawn_after_crash", lambda: None)

    supervisor._wait_for_exit(proc, stdout_thread=_StdoutDrain())

    assert completed and completed[-1]["type"] == "turn.error"
    assert old_control.get_nowait()["type"] == "control.error"
    assert set(supervisor._pending_turns) == {"new-turn"}
    assert supervisor._pending_controls == {"new-control": new_control}
    assert new_control.empty()


def test_crash_breaker_is_applied_before_reentrant_turn_callback_restart(
    tmp_path, monkeypatch
):
    """A queued-turn callback must not spawn a replacement before the breaker opens."""
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        respawn_max=1,
        autostart=False,
    )
    supervisor._restart_times = [time.monotonic()]
    spawned = []
    blocked = []

    class _ExitedProc:
        pid = 1234

        @staticmethod
        def wait():
            return 9

    class _ReplacementProc:
        pid = 5678

        @staticmethod
        def poll():
            return None

    class _StdoutDrain:
        @staticmethod
        def join(timeout=None):
            return None

    old_proc = _ExitedProc()
    supervisor._proc = old_proc
    monkeypatch.setattr(supervisor, "_remove_registry", lambda: None)
    monkeypatch.setattr(supervisor, "reconcile_startup_orphan", lambda: "none")

    def _fake_spawn_locked(*, reason):
        if supervisor._stopped_respawning:
            raise RuntimeError("compute host respawn disabled after crash loop")
        spawned.append(reason)
        supervisor._proc = _ReplacementProc()

    monkeypatch.setattr(supervisor, "_spawn_locked", _fake_spawn_locked)

    def _completion(_frame):
        try:
            supervisor.start()
        except RuntimeError as exc:
            blocked.append(str(exc))

    supervisor._pending_turns["turn-1"] = ("sid-1", _completion)

    supervisor._wait_for_exit(old_proc, stdout_thread=_StdoutDrain())

    assert supervisor._stopped_respawning is True
    assert spawned == []
    assert blocked == ["compute host respawn disabled after crash loop"]
    assert supervisor._proc is None


def test_respawn_max_zero_still_records_crash_cooldown(tmp_path):
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        respawn_max=0,
        autostart=False,
    )

    before = time.monotonic()
    supervisor._maybe_respawn_after_crash()

    assert supervisor._stopped_respawning is True
    assert len(supervisor._restart_times) == 1
    assert supervisor._restart_times[0] >= before


def test_supervisor_crash_breaker_recovers_after_cooldown(tmp_path, monkeypatch):
    """Crash-loop protection must cool down; it must not brick the backend forever."""
    from tui_gateway import host_supervisor as hs

    child_code = (
        "import json, os, sys\n"
        "print(json.dumps({'type':'hello','host_pid':os.getpid(),'boot_id':'cooldown',"
        "'build_sha':'unknown','hermes_home':''}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    if json.loads(line).get('type') == 'shutdown':\n"
        "        break\n"
    )
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-u", "-c", child_code],
        expected_build_sha="unknown",
        expected_hermes_home="",
        respawn_max=1,
        autostart=False,
    )
    monkeypatch.setattr(supervisor, "reconcile_startup_orphan", lambda: "none")
    supervisor._stopped_respawning = True
    supervisor._restart_times = [
        time.monotonic() - hs._RESPAWN_WINDOW_SECS - 1.0
    ]

    try:
        supervisor.start()
        assert supervisor.is_running()
        assert supervisor._stopped_respawning is False
        assert supervisor._restart_times == []
    finally:
        supervisor.shutdown()


def test_supervisor_interrupt_can_wait_for_host_ack(tmp_path, monkeypatch):
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=[sys.executable, "-c", ""],
        autostart=False,
    )
    monkeypatch.setattr(supervisor, "start", lambda: None)
    seen = []
    ack = {"type": "interrupt.ack", "request_id": "stop-1", "applied": True}
    monkeypatch.setattr(
        supervisor,
        "_await_reply",
        lambda frame, request_id, timeout: seen.append((frame, request_id, timeout)) or ack,
    )

    result = supervisor.interrupt("sid-1", request_id="stop-1", wait=True, timeout=2.5)

    assert result == ack
    assert seen == [(
        {"type": "interrupt", "sid": "sid-1", "request_id": "stop-1"},
        "stop-1",
        2.5,
    )]


def test_supervisor_rejects_and_terminates_mismatched_hello(tmp_path, monkeypatch):
    """A child that fails the hello identity contract must not remain registered as the live host."""
    from tui_gateway import host_supervisor as hs

    class _Stdin:
        def write(self, _value):
            return None

        def flush(self):
            return None

    class _FakeProc:
        pid = 43210

        def __init__(self):
            self.stdin = _Stdin()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = None
            self.done = threading.Event()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if not self.done.wait(timeout):
                raise subprocess.TimeoutExpired("fake-compute-host", timeout)
            return self.returncode

        def terminate(self):
            self.returncode = 1
            self.done.set()

        def kill(self):
            self.terminate()

    proc = _FakeProc()
    monkeypatch.setattr(hs.subprocess, "Popen", lambda *args, **kwargs: proc)
    supervisor = HostSupervisor(
        registry_path=tmp_path / "dashboard-compute-host.json",
        argv=["fake-compute-host"],
        expected_build_sha="expected-sha",
        expected_hermes_home="expected-home",
        autostart=False,
    )
    monkeypatch.setattr(supervisor, "reconcile_startup_orphan", lambda: "none")

    def _hello_wait(timeout=None):
        supervisor._hello = {
            "type": "hello", "host_pid": proc.pid, "boot_id": "bad-boot",
            "build_sha": "expected-sha", "hermes_home": "wrong-home",
        }
        return True

    monkeypatch.setattr(supervisor._hello_event, "wait", _hello_wait)

    with pytest.raises(RuntimeError, match="HERMES_HOME mismatch"):
        supervisor.start()

    assert proc.poll() is not None
    assert supervisor._proc is None
    assert not supervisor.registry_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows os.kill(pid, 0) has destructive semantics")
def test_windows_pid_alive_probe_does_not_signal_process():
    from tui_gateway import host_supervisor as hs

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert hs._pid_alive(proc.pid) is True
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_supervisor_startup_reconcile_pid_reuse_guard(tmp_path, monkeypatch):
    registry = tmp_path / "dashboard-compute-host.json"
    registry.write_text(json.dumps({"host_pid": os.getpid(), "boot_id": "stale"}), encoding="utf-8")

    killed: list[int] = []
    supervisor = HostSupervisor(registry_path=registry, argv=[sys.executable, "-c", ""], autostart=False)
    monkeypatch.setattr(supervisor, "_pid_matches_compute_host", lambda _pid: False)
    monkeypatch.setattr(supervisor, "_terminate_pid", lambda pid, **_kw: killed.append(pid))

    result = supervisor.reconcile_startup_orphan()

    assert result == "pid-reuse-ignored"
    assert killed == []
    assert not registry.exists()


def _make_compress_host_session(events: list) -> dict:
    class _Agent:
        model = "host-model"
        provider = "host-provider"
        tools = []
        _cached_system_prompt = ""
        session_input_tokens = 1
        session_output_tokens = 1
        session_prompt_tokens = 1
        session_completion_tokens = 1
        session_total_tokens = 2
        session_api_calls = 1
        session_id = "rotated-id"

    agent = _Agent()
    agent.context_compressor = type("ContextEngineStub", (), {})()
    agent.context_compressor.on_session_start = (
        lambda *_args, **_kwargs: events.append("notify")
    )
    return {
        "agent": agent,
        "session_key": "before-key",
        "history": [
            {"role": "user", "content": "before"},
            {"role": "assistant", "content": "before"},
        ],
        "history_lock": threading.Lock(),
        "history_version": 2,
        "running": False,
        "manual_compression_lock": threading.Lock(),
    }


def _record_finalize(monkeypatch, events: list[str], *sids: str) -> None:
    """Give ``flush_all_sessions`` sessions and record which ones finalize."""
    keys = sids or ("s1",)
    monkeypatch.setattr(
        server,
        "_sessions",
        {sid: {"session_key": sid} for sid in keys},
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_finalize_session",
        lambda _session, end_reason="tui_close": events.append(
            f"finalize:{_session['session_key']}:{end_reason}"
        ),
        raising=False,
    )


def _register_turn(host: ComputeHost, fn, sid: str = "s1") -> None:
    """Submit a turn exactly the way ``_handle_turn_start`` does."""
    host._track_turn_future(host._executor.submit(fn), sid)


def test_shutdown_drains_in_flight_turn_before_finalizing_sessions(monkeypatch):
    events: list[str] = []
    _record_finalize(monkeypatch, events)

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    running = threading.Event()

    def _turn() -> None:
        running.set()
        time.sleep(0.3)
        events.append("turn_end")

    _register_turn(host, _turn, sid="s1")
    assert running.wait(timeout=5.0)

    host.shutdown(reason="sigterm", wait=3.0)

    # ``_finalize_session`` latches on ``session["_finalized"]``, so its single
    # run has to observe the finished turn or the tail is unpersistable. A turn
    # that *did* drain must still finalize — the live-turn skip must not
    # over-reach into sessions whose work is done.
    assert events == ["turn_end", "finalize:s1:compute_host_sigterm"]

    # The done-callback still has to remove the entry now that the container is
    # a dict: ``set.discard`` was a valid bare callback, ``dict.pop`` is not.
    deadline = time.monotonic() + 2.0
    while host._turn_futures and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host._turn_futures == {}, "in-flight turns must not accumulate"


def test_shutdown_retains_a_live_turns_session_when_the_drain_deadline_expires(monkeypatch):
    wait = 1.0
    events: list[str] = []
    _record_finalize(monkeypatch, events, "live", "idle")

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        started = time.monotonic()
        host.shutdown(reason="sigterm", wait=wait)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    # ``_finalize_session`` is one-shot, and the ``shutdown(wait=False)`` that
    # follows does not join the turn. Spending "live"'s single latch mid-turn
    # would leave it permanently un-finalizable and release its active-session
    # lease out from under running work — the same lifecycle race the drain
    # exists to close, just moved past the deadline. It is retained unfinalized
    # for recovery instead. A turn outliving the window must not cost the flush
    # for anyone else, so "idle" still finalizes in the same pass.
    assert events == ["finalize:idle:compute_host_sigterm"]
    assert elapsed < wait


def test_shutdown_retains_live_sessions_within_the_stdin_closed_budget(monkeypatch):
    """The tightest real budget any caller uses is ``wait=2.0``.

    ``run_host`` finalizes through ``host.shutdown(reason="stdin_closed",
    wait=2.0)``, which is where the reserve — ``wait`` minus
    ``min(_FLUSH_RESERVE_SECS, wait / 2)`` — has the least room to work with.
    The retain-live-sessions rule must hold there without costing the flush for
    idle sessions and without pushing the call past the budget the supervisor's
    kill escalation is timed against.
    """
    wait = 2.0
    drain_budget = wait - min(compute_host._FLUSH_RESERVE_SECS, wait / 2.0)

    events: list[str] = []
    _record_finalize(monkeypatch, events, "live", "idle")

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        started = time.monotonic()
        host.shutdown(reason="stdin_closed", wait=wait)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert events == ["finalize:idle:compute_host_stdin_closed"]
    assert elapsed >= drain_budget - 1e-6, "the drain must use its full window"
    assert elapsed < wait


def test_shutdown_drain_sleep_never_overshoots_the_reserve(monkeypatch):
    """The drain's per-tick sleep must be bounded by the time left to it.

    A flat tick overshoots the drain deadline by up to one tick, eating the
    reserve held back for ``flush_all_sessions``; for a small ``wait`` that is
    the whole reserve. Drive a fake monotonic clock from the requested sleeps so
    the assertion is independent of Windows timer granularity (real short sleeps
    may return early).
    """
    wait = 0.34
    drain_budget = wait - min(compute_host._FLUSH_RESERVE_SECS, wait / 2.0)

    events: list[str] = []
    _record_finalize(monkeypatch, events, "idle")

    slept: list[float] = []
    now = [100.0]

    def _recording_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    class _FakeTime:
        @staticmethod
        def monotonic() -> float:
            return now[0]

        sleep = staticmethod(_recording_sleep)

    # Replace only compute_host's module reference; mutating the shared stdlib ``time``
    # module leaks the fake monotonic clock into host_supervisor tests that run later.
    monkeypatch.setattr(compute_host, "time", _FakeTime)

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        host.shutdown(reason="sigterm", wait=wait)
    finally:
        release.set()

    assert events == ["finalize:idle:compute_host_sigterm"]
    assert slept, "the drain loop should have ticked at least once"
    assert sum(slept) <= drain_budget + 1e-6
