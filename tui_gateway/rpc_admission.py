"""Bounded admission for slow (pool-routed) gateway RPC methods — F1.3, opt-in.

Problem (DISCOVERY-1 C3): slow methods go through ``server._pool`` (default 8
workers) whose internal queue is unbounded. A burst of slow calls — multi-window
polling, a slow provider — is accepted in milliseconds and piles up behind the
workers; nothing is rejected, per-request latency grows without bound, and
requests whose client long gave up still execute their side effects.

``server.dispatch`` consults :func:`current_admission` for ``_LONG_HANDLERS``
methods ONLY when the flag ``tui_gateway.rpc_admission.enabled`` is on
(default OFF; flag OFF keeps the historical unbounded ``_pool.submit`` path
unchanged). Inline control-path methods (interrupt, approval responses,
ping/status, ...) never run through the pool and are never queued behind or
rejected by this gate.

Wire contract (for a later client-side retry task; also in RESULT-f1-3.md):

* queue full → JSON-RPC error ``4028`` ``rpc busy: slow-method queue is full``,
  ``data.retry_after_ms`` = drain estimate for the CURRENT backlog (see
  :meth:`BoundedGate.retry_after_ms`).
* admitted but not started before ``queue_deadline_ms`` → dropped WITHOUT
  executing; the client receives ``4029`` ``rpc expired: queued request
  exceeded its deadline before starting`` with the same ``data`` shape. The
  handler itself is never entered, so stale side effects never run after the
  client gave up.

All state is process-local and thread-safe; nothing is persisted. The config
is re-read at most once per ``_CFG_TTL_S`` so config reloads are picked up
without a gateway restart while a request burst cannot turn into config I/O.
"""

from __future__ import annotations

import threading
import time

# Stable JSON-RPC error codes in the gateway's private 4xxx range (4028/4029
# were unused). These numbers are the client-retry contract — do not renumber.
RPC_BUSY_CODE = 4028
RPC_EXPIRED_CODE = 4029

# Cache the parsed flag between requests: a 2000-request burst must not turn
# into 2000 config reads, while still picking up reloads within seconds.
_CFG_TTL_S = 2.0

BUSY_MESSAGE = "rpc busy: slow-method queue is full"
EXPIRED_MESSAGE = "rpc expired: queued request exceeded its deadline before starting"


class AdmissionConfig:
    """Parsed ``tui_gateway.rpc_admission`` section; defaults = flag OFF."""

    __slots__ = ("enabled", "max_queue", "queue_deadline_ms")

    def __init__(self, enabled: bool = False, max_queue: int = 64, queue_deadline_ms: int = 5000):
        self.enabled = enabled
        self.max_queue = max_queue
        self.queue_deadline_ms = queue_deadline_ms

    @classmethod
    def from_cfg(cls, cfg: dict) -> "AdmissionConfig":
        section = (cfg.get("tui_gateway") or {}) if isinstance(cfg, dict) else {}
        raw = section.get("rpc_admission") if isinstance(section, dict) else None
        if not isinstance(raw, dict):
            return cls(enabled=False)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            max_queue=_positive_int(raw.get("max_queue"), default=64, floor=1),
            queue_deadline_ms=_positive_int(raw.get("queue_deadline_ms"), default=5000, floor=100),
        )

    def gate_key(self) -> tuple:
        return (self.enabled, self.max_queue, self.queue_deadline_ms)


def _positive_int(v, *, default: int, floor: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if n >= floor else default


class BoundedGate:
    """Bounded waiting-room in front of the slow-method pool.

    One semaphore permit per request waiting for a worker: acquired at
    admission, released when the handler STARTS. The pool's internal queue
    therefore never holds more than ``max_queue`` gate-submitted items —
    running handlers hold no permits, so the cap bounds the backlog exactly.
    """

    def __init__(self, workers: int, max_queue: int, queue_deadline_ms: float):
        self.workers = workers
        self.max_queue = max_queue
        self.queue_deadline_s = queue_deadline_ms / 1000.0
        self._sem = threading.BoundedSemaphore(max_queue)
        self._lock = threading.Lock()
        self._seq = 0
        self._waiting: dict[int, float] = {}  # seq -> enqueue monotonic time

    def try_submit(self, run, submit, on_expired) -> tuple[bool, dict | None]:
        """Admit ``run`` behind the gate; on a full gate return the busy error.

        ``run`` is the zero-arg callable the pool executes (it writes its own
        response). ``submit`` is ``pool.submit``. ``on_expired`` receives the
        ready-to-write 4029 envelope when an admitted request is dropped after
        waiting out its deadline (called on the pool worker thread). The
        acquire is non-blocking: under saturation the dispatch thread must
        answer immediately, never park the RPC read loop.
        """
        if not self._sem.acquire(blocking=False):
            return False, _error(RPC_BUSY_CODE, self.retry_after_ms())
        with self._lock:
            self._seq += 1
            seq = self._seq
            enqueued_at = time.monotonic()
            self._waiting[seq] = enqueued_at
        deadline = enqueued_at + self.queue_deadline_s

        def wrapper():
            started = time.monotonic()
            with self._lock:
                self._waiting.pop(seq, None)
            self._sem.release()
            if started > deadline:
                on_expired(_error(RPC_EXPIRED_CODE, self.retry_after_ms()))
                return None  # expired while queued: the handler must NOT run
            return run()

        submit(wrapper)
        return True, None

    def retry_after_ms(self) -> int:
        """Conservative drain estimate for the current backlog, in ms.

        No queue item waits longer than ``queue_deadline_ms`` (it is dropped),
        so ``ceil(backlog / workers)`` deadline-length rounds bound how long
        the LAST queued item still has to wait; the oldest item's already-waited
        time is subtracted from its own remaining round. Result clamped to
        [queue_deadline_ms, 60 s] — a floor because per-item service time is
        not measured (the gate does not own pool telemetry), a cap so a huge
        backlog cannot advise an absurd sleep.
        """
        with self._lock:
            backlog = len(self._waiting)
            oldest = min(self._waiting.values()) if self._waiting else None
        if not backlog or oldest is None:
            return int(self.queue_deadline_s * 1000)
        waited = time.monotonic() - oldest
        rounds = (backlog + self.workers - 1) // self.workers
        estimate_s = max(rounds - 1, 0) * self.queue_deadline_s + max(self.queue_deadline_s - waited, 0.0)
        return int(min(max(estimate_s * 1000.0, self.queue_deadline_s * 1000.0), 60_000.0))

    def qsize(self) -> int:
        """Requests currently waiting for a worker (admitted, not started)."""
        with self._lock:
            return len(self._waiting)


def _error(code: int, retry_after_ms: int) -> dict:
    message = BUSY_MESSAGE if code == RPC_BUSY_CODE else EXPIRED_MESSAGE
    return {"code": code, "message": message, "data": {"retry_after_ms": max(1, int(retry_after_ms))}}


# -- process-wide gate cache ---------------------------------------------------
#
# One gate per distinct config; a config change swaps in a new gate while
# in-flight requests finish on the old one. ``load_cfg`` is injected by
# server.py (its ``_load_cfg``) to keep this module import-light for tests.

_gates: dict[tuple, BoundedGate] = {}
_gates_lock = threading.Lock()
_cached: tuple[float, AdmissionConfig] | None = None
_cached_lock = threading.Lock()


def current_admission(load_cfg, workers: int) -> AdmissionConfig | None:
    """Enabled :class:`AdmissionConfig` or None (flag off / unreadable config).

    ``load_cfg`` must return the effective user config dict; failures inside
    it are expected to be suppressed by the caller's own fail-open contract.
    Result cached for ``_CFG_TTL_S``.
    """
    global _cached
    now = time.monotonic()
    with _cached_lock:
        if _cached is not None and now - _cached[0] < _CFG_TTL_S:
            return _cached[1] if _cached[1].enabled else None
    try:
        cfg = AdmissionConfig.from_cfg(load_cfg() or {})
    except Exception:
        cfg = AdmissionConfig(enabled=False)
    with _cached_lock:
        _cached = (now, cfg)
    return cfg if cfg.enabled else None


def gate_for(cfg: AdmissionConfig, workers: int) -> BoundedGate:
    key = (*cfg.gate_key(), workers)
    with _gates_lock:
        gate = _gates.get(key)
        if gate is None:
            gate = BoundedGate(workers, cfg.max_queue, cfg.queue_deadline_ms)
            _gates[key] = gate
        return gate


def reset_for_tests() -> None:
    """Drop cached config and gates so tests can flip the flag per-case."""
    global _cached
    with _cached_lock:
        _cached = None
    with _gates_lock:
        _gates.clear()
