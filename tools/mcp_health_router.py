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
    samples: int = 0
    recent_success_rate: Optional[float] = None
    ewma_latency_ms: Optional[int] = None
    approval_required: bool = False
    auth_type: str = ""

    def as_dict(self) -> dict:
        out = {
            "status": self.status,
            "score": round(float(self.score), 3),
            "server": self.server,
        }
        if self.failures:
            out["failures"] = int(self.failures)
        if self.retry_in is not None:
            out["retry_in_seconds"] = int(self.retry_in)
        if self.samples:
            out["samples"] = int(self.samples)
        if self.recent_success_rate is not None:
            out["recent_success_rate"] = round(float(self.recent_success_rate), 3)
        if self.ewma_latency_ms is not None:
            out["ewma_latency_ms"] = int(self.ewma_latency_ms)
        if self.approval_required:
            out["approval_required"] = True
        if self.auth_type:
            out["auth_type"] = self.auth_type
        return out


def _seconds_left(deadline: float, now: float) -> int:
    return max(1, int(math.ceil(deadline - now)))


def _latency_factor(latency_ms: float) -> float:
    if latency_ms <= 500:
        return 1.0
    if latency_ms <= 1500:
        return 0.98
    if latency_ms <= 5000:
        return 0.92
    if latency_ms <= 15000:
        return 0.82
    return 0.70


def _apply_route_quality(base: MCPToolHealth, tool_name: str, key, server, core) -> MCPToolHealth:
    """Blend stable runtime health with bounded recent RPC quality evidence."""
    from tools.mcp_tool_scope import _server_key
    from tools.mcp_route_metrics import snapshot

    metrics = snapshot(base.server, route_token=id(server) if server is not None else None)
    auth_type = str(
        getattr(server, "_auth_type", "")
        or (getattr(server, "_config", {}) or {}).get("auth")
        or ""
    ).strip().lower()

    trust = core._server_trust_levels.get(_server_key(base.server), core._TRUST_FULL)
    read_only = core._tool_read_only_hints.get(key, {}).get(tool_name) is True
    approval_required = trust == core._TRUST_UNTRUSTED and not read_only

    factor = 0.96 if approval_required else 1.0
    status = base.status
    samples = 0
    recent_success_rate = None
    latency_ms = None
    if metrics is not None:
        samples = metrics.samples
        recent_success_rate = metrics.recent_success_rate
        latency_ms = int(round(metrics.ewma_latency_ms))
        if samples >= 3:
            factor *= 0.70 + 0.30 * recent_success_rate
            factor *= _latency_factor(metrics.ewma_latency_ms)
            if status in {"healthy", "lazy", "recycled", "warming"}:
                if recent_success_rate < 0.75:
                    status = "route_degraded"
                elif metrics.ewma_latency_ms >= 5000:
                    status = "slow"
    if approval_required and status in {"healthy", "lazy", "recycled", "warming"}:
        status = "approval_required"

    return MCPToolHealth(
        score=max(0.01, min(1.0, base.score * factor)),
        status=status,
        server=base.server,
        failures=base.failures,
        retry_in=base.retry_in,
        samples=samples,
        recent_success_rate=recent_success_rate,
        ewma_latency_ms=latency_ms,
        approval_required=approval_required,
        auth_type=auth_type,
    )


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

    def finish(base: MCPToolHealth) -> MCPToolHealth:
        return _apply_route_quality(base, tool_name, key, server, core)

    threshold = int(core._CIRCUIT_BREAKER_THRESHOLD)
    cooldown = float(core._CIRCUIT_BREAKER_COOLDOWN_SEC)
    if failures >= threshold and opened_at:
        age = max(0.0, now - opened_at)
        if age < cooldown:
            return finish(MCPToolHealth(
                0.25 if app_errors else 0.05,
                "breaker_open_application" if app_errors else "breaker_open",
                server_name,
                failures,
                max(1, int(math.ceil(cooldown - age))),
            ))
        return finish(MCPToolHealth(
            0.65 if app_errors else 0.5, "half_open", server_name, failures
        ))

    if retry_after > now and (server is None or session is None):
        return finish(MCPToolHealth(
            0.08, "connect_cooldown", server_name, failures,
            _seconds_left(retry_after, now),
        ))
    if connecting:
        return finish(MCPToolHealth(0.35, "connecting", server_name, failures))
    if suspect:
        return finish(MCPToolHealth(0.4, "suspect", server_name, failures))
    if reconnecting:
        return finish(MCPToolHealth(0.4, "reconnecting", server_name, failures))

    if server is None:
        if lazy:
            return finish(MCPToolHealth(0.88, "lazy", server_name, failures))
        return finish(MCPToolHealth(0.2, "unavailable", server_name, failures))

    if session is None:
        if recycled:
            return finish(MCPToolHealth(0.85, "recycled", server_name, failures))
        return finish(MCPToolHealth(0.3, "disconnected", server_name, failures))

    if failures:
        score = max(0.6, 0.85 - 0.12 * failures)
        return finish(MCPToolHealth(score, "degraded", server_name, failures))

    if not proven:
        return finish(MCPToolHealth(0.92, "warming", server_name))

    return finish(MCPToolHealth(1.0, "healthy", server_name))


def routing_weight(tool_name: str) -> float:
    """Scalar Tool Search multiplier; non-MCP tools remain neutral."""
    health = tool_health(tool_name)
    return 1.0 if health is None else health.score
