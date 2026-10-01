"""Read-only MCP health snapshots for capability routing.

This module never probes, reconnects, or mutates MCP state. It translates the runtime's
existing breaker/connect/session ledgers into a small score used by Tool Search so a healthy
alternative can outrank a temporarily broken MCP server without hiding the broken tool.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MCPToolHealth:
    score: float
    status: str
    server: str
    failures: int = 0
    retry_in: Optional[int] = None

    def as_dict(self) -> dict:
        out = {
            "status": self.status,
            "score": round(float(self.score), 3),
            "server": self.server,
        }
        if self.failures:
            out["failures"] = int(self.failures)
        if self.retry_in is not None:
            out["retry_in"] = int(self.retry_in)
        return out


def _seconds_left(deadline: float, now: float) -> int:
    return max(1, int(math.ceil(deadline - now)))


def tool_health(tool_name: str, *, now: Optional[float] = None) -> Optional[MCPToolHealth]:
    """Return the current routing health for one registered MCP tool.

    None means the tool is not known to the MCP provenance map. Scores are routing hints,
    not availability gates: exact-name Tool Search queries still win, and the call-time MCP
    circuit breaker remains the authority for whether execution may proceed.
    """
    from tools import mcp_tool as core
    from tools.mcp_tool_scope import _resolve_server_key

    now = time.monotonic() if now is None else float(now)
    with core._lock:
        server_name = core._mcp_tool_server_names.get(tool_name)
        if not server_name:
            return None
        key = _resolve_server_key(server_name)
        server = core._servers.get(key)
        failures = int(core._server_error_counts.get(key, 0) or 0)
        opened_at = float(core._server_breaker_opened_at.get(key, 0.0) or 0.0)
        app_errors = bool(core._server_errors_all_application.get(key))
        retry_after = float(core._server_connect_retry_after.get(key, 0.0) or 0.0)
        connecting = key in core._server_connecting
        lazy = key in core._lazy_server_configs

        session = getattr(server, "session", None) if server is not None else None
        suspect = bool(getattr(server, "_suspect_reason", None)) if server is not None else False
        reconnecting = bool(getattr(server, "_reconnecting", False)) if server is not None else False
        proven = bool(getattr(server, "_session_proven", False)) if server is not None else False
        recycled = False
        if server is not None:
            is_recycled = getattr(server, "_is_recycled_stdio", None)
            if callable(is_recycled):
                try:
                    recycled = bool(is_recycled())
                except Exception:
                    recycled = False

    threshold = int(core._CIRCUIT_BREAKER_THRESHOLD)
    cooldown = float(core._CIRCUIT_BREAKER_COOLDOWN_SEC)
    if failures >= threshold and opened_at:
        age = max(0.0, now - opened_at)
        if age < cooldown:
            return MCPToolHealth(
                0.25 if app_errors else 0.05,
                "breaker_open_application" if app_errors else "breaker_open",
                server_name,
                failures,
                max(1, int(math.ceil(cooldown - age))),
            )
        return MCPToolHealth(0.65 if app_errors else 0.5, "half_open", server_name, failures)

    if retry_after > now and (server is None or session is None):
        return MCPToolHealth(
            0.08, "connect_cooldown", server_name, failures,
            _seconds_left(retry_after, now),
        )
    if connecting:
        return MCPToolHealth(0.35, "connecting", server_name, failures)
    if suspect:
        return MCPToolHealth(0.4, "suspect", server_name, failures)
    if reconnecting:
        return MCPToolHealth(0.4, "reconnecting", server_name, failures)

    if server is None:
        if lazy:
            return MCPToolHealth(0.88, "lazy", server_name, failures)
        return MCPToolHealth(0.2, "unavailable", server_name, failures)

    if session is None:
        if recycled:
            return MCPToolHealth(0.85, "recycled", server_name, failures)
        return MCPToolHealth(0.3, "disconnected", server_name, failures)

    if failures:
        score = max(0.6, 0.85 - 0.12 * failures)
        return MCPToolHealth(score, "degraded", server_name, failures)

    if not proven:
        return MCPToolHealth(0.92, "warming", server_name)

    return MCPToolHealth(1.0, "healthy", server_name)


def routing_weight(tool_name: str) -> float:
    """Scalar Tool Search multiplier; non-MCP tools remain neutral."""
    health = tool_health(tool_name)
    return 1.0 if health is None else health.score
