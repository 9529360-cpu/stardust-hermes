"""Bounded admission for slow (pool-routed) RPC methods — F1.3.

Contract under ``tui_gateway.rpc_admission.enabled`` (default OFF):

* dispatch() of a ``_LONG_HANDLERS`` method goes through the bounded gate: at
  most ``max_queue`` requests wait for a worker; the excess is answered
  synchronously with the 4028 busy error carrying ``data.retry_after_ms``.
* an admitted request that does not START within ``queue_deadline_ms`` is
  dropped — the handler never runs — and the transport receives 4029.
* inline control-path methods (ping, interrupt, approval responses) never
  touch the gate and are answered even while the gate is saturated.
* flag OFF keeps the historical unbounded path (no behaviour change).
"""

import io
import json
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout_and_gate():
    yield
    sys.stdout = _original_stdout
    __import__("tui_gateway.rpc_admission", fromlist=["x"]).reset_for_tests()


@pytest.fixture()
def server():
    # Import isolation mirrors test_inline_rpc_gil_starvation.py: mocks scoped
    # to the initial import only, then the real module state is snapshotted.
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value="/tmp/hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")

    methods = dict(mod._methods)
    real_stdout = mod._real_stdout
    yield mod
    mod._methods.clear()
    mod._methods.update(methods)
    mod._real_stdout = real_stdout
    mod._sessions.clear()
    __import__("tui_gateway.server_requests", fromlist=["x"]).reset_for_tests()
    __import__("tui_gateway.rpc_admission", fromlist=["x"]).reset_for_tests()
    # The module pool is shared process-wide: never shutdown() it here (that
    # silently swallows every later test's submits) and never join its worker
    # threads (pool workers idle forever). Each test's handlers are bounded by
    # release.wait(seconds), so the pool drains on its own.


class Sink:
    """Collects worker-written frames with arrival timestamps."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frames = []

    def write(self, obj):
        with self.lock:
            self.frames.append((time.monotonic(), obj))
        return True

    def by_id(self, rid):
        with self.lock:
            for _, f in self.frames:
                if f.get("id") == rid:
                    return f
        return None


def _enable(server, monkeypatch, max_queue=4, deadline_ms=200, enabled=True):
    monkeypatch.setattr(server, "_load_cfg", lambda: {
        "tui_gateway": {"rpc_admission": {
            "enabled": enabled, "max_queue": max_queue, "queue_deadline_ms": deadline_ms}}})
    import tui_gateway.rpc_admission as ra
    ra.reset_for_tests()


def _slow_method(server, seconds, release=None):
    started = threading.Event()
    if release is None:
        release = threading.Event()
    calls = []
    start_events = {}  # rid -> per-request started event
    start_lock = threading.Lock()

    def handler(rid, params):
        calls.append(rid)
        with start_lock:
            ev = start_events.setdefault(rid, threading.Event())
        ev.set()
        started.set()
        release.wait(seconds)  # bounded either by release or by seconds
        return server._ok(rid, {"text": "synthetic"})  # LlmOneshotResult contract shape

    server._methods["llm.oneshot"] = handler

    def wait_started(rids, timeout=5.0):
        """Block until every rid's handler has STARTED (permits released)."""
        deadline = time.monotonic() + timeout
        for rid in rids:
            with start_lock:
                ev = start_events.get(rid)
            if ev is None or not ev.wait(timeout=max(0.0, deadline - time.monotonic())):
                return False
        return True

    return started, release, calls, wait_started


# ─── config parsing ────────────────────────────────────────────────────────


def test_admission_config_defaults_off(server, monkeypatch):
    import tui_gateway.rpc_admission as ra
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    ra.reset_for_tests()
    assert ra.current_admission(server._load_cfg, server._rpc_pool_workers) is None


def test_admission_config_parses_section(server, monkeypatch):
    import tui_gateway.rpc_admission as ra
    monkeypatch.setattr(server, "_load_cfg", lambda: {
        "tui_gateway": {"rpc_admission": {"enabled": True, "max_queue": 7, "queue_deadline_ms": 1234}}})
    ra.reset_for_tests()
    cfg = ra.current_admission(server._load_cfg, server._rpc_pool_workers)
    assert cfg is not None and cfg.max_queue == 7 and cfg.queue_deadline_ms == 1234


def test_admission_config_garbage_fails_closed(server, monkeypatch):
    import tui_gateway.rpc_admission as ra
    monkeypatch.setattr(server, "_load_cfg", lambda: {
        "tui_gateway": {"rpc_admission": {"enabled": "yes please", "max_queue": -3}}})
    ra.reset_for_tests()
    # enabled is not a bool-ish true → off; garbage numbers fall back to defaults
    cfg = ra.current_admission(server._load_cfg, server._rpc_pool_workers)
    assert cfg is None or (cfg.max_queue == 64 and cfg.queue_deadline_ms == 5000)


# ─── flag OFF: no behaviour change ─────────────────────────────────────────


def test_flag_off_keeps_unbounded_path(server, monkeypatch):
    _enable(server, monkeypatch, enabled=False)
    started, release, calls, wait_started = _slow_method(server, 5.0)
    sink = Sink()
    # workers + excess all get submitted: nothing is rejected with the flag off
    n = server._rpc_pool_workers + 6
    for i in range(n):
        resp = server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
        assert resp is None  # queued on the pool, no synchronous error
    release.set()
    deadline = time.monotonic() + 10
    while len(calls) < n and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(calls) == n  # every request ran — the historical behaviour


# ─── flag ON: busy error at the bound ──────────────────────────────────────


def test_flag_on_rejects_excess_with_busy_error(server, monkeypatch):
    _enable(server, monkeypatch, max_queue=4, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    w = server._rpc_pool_workers
    # phase 1: fill every worker (permits free once handlers START)
    for i in range(w):
        assert server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink) is None
    assert wait_started(list(range(w))), "workers never started"
    # phase 2: exactly max_queue admitted into the waiting room
    for i in range(w, w + 4):
        assert server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink) is None
    # phase 3: workers blocked, waiting room full -> every excess is busy 4028
    busy = []
    t0 = time.perf_counter()
    for i in range(w + 4, w + 14):
        t_submit = time.perf_counter()
        resp = server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
        assert resp is not None and resp["error"]["code"] == 4028
        busy.append((time.perf_counter() - t_submit) * 1000)
        err = resp["error"]
        assert "queue is full" in err["message"]
        assert isinstance(err["data"]["retry_after_ms"], int) and err["data"]["retry_after_ms"] >= 1
        assert "result" not in resp
    assert max(busy) < 50.0, f"slowest busy rejection {max(busy):.1f} ms exceeds the 50 ms budget"
    release.set()
    deadline = time.monotonic() + 10
    while len(calls) < w + 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(calls) == w + 4  # only the admitted ones executed


def test_busy_error_is_immediate(server, monkeypatch):
    """The busy answer must arrive well under the 50 ms budget per rejection."""
    _enable(server, monkeypatch, max_queue=2, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    w = server._rpc_pool_workers
    for i in range(w):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    assert wait_started(list(range(w)))
    for i in range(w, w + 2):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    lat = []
    for i in range(200):
        t0 = time.perf_counter()
        resp = server.dispatch({"jsonrpc": "2.0", "id": 1000 + i, "method": "llm.oneshot", "params": {}}, sink)
        lat.append((time.perf_counter() - t0) * 1000)
        assert resp is not None and resp["error"]["code"] == 4028
    release.set()
    p99 = sorted(lat)[int(len(lat) * 0.99)]
    assert p99 < 50.0, f"busy rejection p99 {p99:.1f} ms exceeds the 50 ms budget"


def test_flag_on_queue_never_exceeds_max(server, monkeypatch):
    import tui_gateway.rpc_admission as ra
    _enable(server, monkeypatch, max_queue=4, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    w = server._rpc_pool_workers
    for i in range(w):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    assert wait_started(list(range(w)))
    cfg_gate = ra.current_admission(server._load_cfg, w)
    gate = ra.gate_for(cfg_gate, w)
    for i in range(400):
        server.dispatch({"jsonrpc": "2.0", "id": w + i, "method": "llm.oneshot", "params": {}}, sink)
        assert gate.qsize() <= 4
    release.set()


# ─── flag ON: deadline drop ────────────────────────────────────────────────


def test_expired_request_never_executes_and_answers_4029(server, monkeypatch):
    _enable(server, monkeypatch, max_queue=8, deadline_ms=150)
    # workers busy for a long time; queued items wait past the 150 ms deadline
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    w = server._rpc_pool_workers
    for i in range(w):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    assert wait_started(list(range(w)))
    for i in range(w, w + 4):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    time.sleep(0.6)  # > deadline: the 4 queued requests must expire
    release.set()
    deadline = time.monotonic() + 5
    while len(sink.frames) < 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    expired = [f for _, f in sink.frames if "error" in f and f["error"].get("code") == 4029]
    assert len(expired) == 4
    for f in expired:
        assert f["error"]["data"]["retry_after_ms"] >= 1
    # only the FIRST workers batch actually executed the handler
    assert len(calls) == w


def test_within_deadline_still_runs(server, monkeypatch):
    _enable(server, monkeypatch, max_queue=8, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 0.05)
    sink = Sink()
    for i in range(server._rpc_pool_workers + 2):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    deadline = time.monotonic() + 5
    done = 0
    while time.monotonic() < deadline:
        done = sum(1 for _, f in sink.frames if "result" in f)
        if done == server._rpc_pool_workers + 2:
            break
        time.sleep(0.05)
    assert done == server._rpc_pool_workers + 2
    assert all("error" not in f for _, f in sink.frames)


# ─── control path stays inline ─────────────────────────────────────────────


def test_ping_not_gated_under_saturation(server, monkeypatch):
    _enable(server, monkeypatch, max_queue=2, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    for i in range(server._rpc_pool_workers + 10):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    t0 = time.perf_counter()
    resp = server.dispatch({"jsonrpc": "2.0", "id": "ctl", "method": "ping", "params": {}})
    dt = (time.perf_counter() - t0) * 1000
    release.set()
    assert resp is not None and resp.get("result", {}).get("pong") is True
    assert dt < 200.0, f"ping answered in {dt:.1f} ms under saturation"


def test_response_frame_not_gated(server, monkeypatch):
    """approval.respond-style frames resolve inline, gate saturated or not."""
    _enable(server, monkeypatch, max_queue=2, deadline_ms=5_000)
    started, release, calls, wait_started = _slow_method(server, 30.0)
    sink = Sink()
    for i in range(server._rpc_pool_workers + 10):
        server.dispatch({"jsonrpc": "2.0", "id": i, "method": "llm.oneshot", "params": {}}, sink)
    frame = {"jsonrpc": "2.0", "id": "srq-1", "result": {"choice": "allow"}}
    resp = server.dispatch(frame, sink)  # must not raise, must not be queued
    release.set()
    assert resp is None


# ─── retry_after_ms sanity ─────────────────────────────────────────────────


def test_retry_after_ms_bounded(server, monkeypatch):
    import tui_gateway.rpc_admission as ra
    gate = ra.BoundedGate(workers=8, max_queue=4, queue_deadline_ms=500)
    # empty backlog → at least the deadline
    assert gate.retry_after_ms() == 500
    ra.reset_for_tests()


def test_gate_sem_layout():
    """Pure gate mechanics driven by a manual submit queue (no real pool).

    Submitting ONE running item + ``max_queue`` waiters must fill the gate;
    the next is rejected 4028; expiring waiters answer 4029 via the callback
    without the run() body executing.
    """
    import tui_gateway.rpc_admission as ra
    gate = ra.BoundedGate(workers=8, max_queue=3, queue_deadline_ms=80)
    submitted = []   # wrappers captured instead of executing them
    ran = []
    expired_frames = []

    def fake_submit(wrapper):
        submitted.append(wrapper)

    # first item goes straight through: fake pool "runs" it immediately
    ok, err = gate.try_submit(lambda: ran.append("running"), fake_submit, expired_frames.append)
    assert ok and err is None
    submitted.pop(0)()  # pool starts it now: permit already released
    assert gate.qsize() == 0  # running holds no permit
    for i, expect_q in enumerate([1, 2, 3], start=1):  # fill the waiting room
        ok, err = gate.try_submit(lambda: ran.append(i), fake_submit, expired_frames.append)
        assert ok and gate.qsize() == expect_q
    ok, err = gate.try_submit(lambda: ran.append("x"), fake_submit, expired_frames.append)
    assert ok is False and err["code"] == 4028 and err["data"]["retry_after_ms"] >= 1
    assert gate.qsize() == 3
    assert isinstance(gate.retry_after_ms(), int)

    time.sleep(0.4)  # waiters now comfortably past the 80 ms deadline
    for wrapper in list(submitted):  # every waiter: pool would run them now → expired
        wrapper()  # expired waiters answer 4029; handler NOT executed
    assert ran == ["running"]
    assert len(expired_frames) == 3
    assert all(f["code"] == 4029 for f in expired_frames)
    assert gate.qsize() == 0
    # room freed: a fresh request is admitted again
    ok, err = gate.try_submit(lambda: ran.append("fresh"), fake_submit, expired_frames.append)
    assert ok and gate.qsize() == 1
    submitted[-1]()  # immediate start: within deadline → executes
    assert ran == ["running", "fresh"]
    ra.reset_for_tests()
