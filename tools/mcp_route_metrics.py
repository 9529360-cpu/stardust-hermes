"""Process-local MCP route-quality telemetry.

Only aggregate call timing/outcome is kept: no arguments, tool results, URLs, credentials, or user
content. The ledger is intentionally ephemeral and follows the live connection key so profile scope
resolution remains owned by mcp_tool_scope.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


_EWMA_ALPHA = 0.25
_lock = threading.Lock()


@dataclass(frozen=True)
class MCPRouteSnapshot:
    samples: int
    successes: int
    failures: int
    recent_success_rate: float
    ewma_latency_ms: float
    last_success_age_seconds: float | None = None
    last_failure_age_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "samples": self.samples,
            "recent_success_rate": round(self.recent_success_rate, 3),
            "ewma_latency_ms": int(round(self.ewma_latency_ms)),
        }
        if self.last_failure_age_seconds is not None:
            out["last_failure_age_seconds"] = int(max(0, round(self.last_failure_age_seconds)))
        return out


@dataclass
class _MutableMetrics:
    samples: int = 0
    successes: int = 0
    failures: int = 0
    ewma_success: float = 1.0
    ewma_latency_s: float = 0.0
    last_success_at: float | None = None
    last_failure_at: float | None = None


_metrics: dict[Any, _MutableMetrics] = {}


def _server_key(server_name: str):
    from tools.mcp_tool_scope import _resolve_server_key
    return _resolve_server_key(server_name)


def record_call(
    server_name: str,
    *,
    elapsed_seconds: float,
    success: bool,
    now: float | None = None,
) -> None:
    """Record one completed logical dispatch. User interrupts should not call this function."""
    timestamp = time.monotonic() if now is None else float(now)
    latency = max(0.0, float(elapsed_seconds))
    key = _server_key(server_name)
    with _lock:
        row = _metrics.setdefault(key, _MutableMetrics())
        row.samples += 1
        if success:
            row.successes += 1
            row.last_success_at = timestamp
        else:
            row.failures += 1
            row.last_failure_at = timestamp

        point = 1.0 if success else 0.0
        if row.samples == 1:
            row.ewma_success = point
            row.ewma_latency_s = latency
        else:
            row.ewma_success = (
                _EWMA_ALPHA * point + (1.0 - _EWMA_ALPHA) * row.ewma_success
            )
            row.ewma_latency_s = (
                _EWMA_ALPHA * latency + (1.0 - _EWMA_ALPHA) * row.ewma_latency_s
            )


def snapshot(server_name: str, *, now: float | None = None) -> MCPRouteSnapshot | None:
    timestamp = time.monotonic() if now is None else float(now)
    key = _server_key(server_name)
    with _lock:
        row = _metrics.get(key)
        if row is None:
            return None
        return MCPRouteSnapshot(
            samples=row.samples,
            successes=row.successes,
            failures=row.failures,
            recent_success_rate=max(0.0, min(1.0, row.ewma_success)),
            ewma_latency_ms=max(0.0, row.ewma_latency_s * 1000.0),
            last_success_age_seconds=(
                None if row.last_success_at is None else max(0.0, timestamp - row.last_success_at)
            ),
            last_failure_age_seconds=(
                None if row.last_failure_at is None else max(0.0, timestamp - row.last_failure_at)
            ),
        )


def clear(server_name: str | None = None) -> None:
    """Test/teardown helper; never persisted."""
    with _lock:
        if server_name is None:
            _metrics.clear()
        else:
            _metrics.pop(_server_key(server_name), None)
