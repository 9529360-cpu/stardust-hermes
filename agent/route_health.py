"""Persistent provider/model route health for cross-session failover.

The existing credential pool owns failures of one credential inside a provider.  This
module owns the next level up: the health of a provider/model/base-URL route.  State is
profile-scoped, contains no credentials, and is advisory/fail-open so a corrupt or
unwritable health file can never make the agent unavailable.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.error_classifier import FailoverReason
from hermes_constants import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_STATE_VERSION = 1
_DEFAULT_PROBE_LEASE_S = 45
_MAX_COOLDOWN_S = 24 * 60 * 60
_STATE_LOCK_TIMEOUT_S = 2.0
_LOCK = threading.RLock()
_STATE_LOCK_HOLDER = threading.local()

_BASE_COOLDOWNS = {
    FailoverReason.auth: 60 * 60,
    FailoverReason.auth_permanent: 24 * 60 * 60,
    FailoverReason.billing: 4 * 60 * 60,
    FailoverReason.rate_limit: 60,
    FailoverReason.upstream_rate_limit: 60,
    FailoverReason.overloaded: 120,
    FailoverReason.server_error: 60,
    FailoverReason.timeout: 30,
    FailoverReason.model_not_found: 24 * 60 * 60,
    FailoverReason.provider_policy_blocked: 60 * 60,
    FailoverReason.unknown: 30,
}


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        raw = (load_config() or {}).get("route_failover") or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_config().get("persistent_health", True))


def state_path() -> Path:
    configured = str(_config().get("health_file") or "").strip()
    return Path(configured).expanduser() if configured else get_hermes_home() / "route-health.json"


def route_identity(provider: str, model: str, base_url: str = "") -> tuple[str, dict[str, str]]:
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    raw_url = str(base_url or "").strip()
    try:
        parsed = urlsplit(raw_url)
        # Never persist userinfo, query parameters, or fragments: custom endpoint URLs sometimes
        # carry credentials there. Hostname is case-insensitive; path/model spelling is not.
        host = (parsed.hostname or "").lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        base_url = urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))
    except (ValueError, AttributeError):
        base_url = ""
    # JSON encoding avoids delimiter ambiguity while keeping the on-disk state operator-readable.
    key = json.dumps([provider, model, base_url], ensure_ascii=True, separators=(",", ":"))
    return key, {"provider": provider, "model": model, "base_url": base_url}


def _empty_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "routes": {}}


def _read_state() -> dict[str, Any]:
    path = state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("routes"), dict):
            return _empty_state()
        return raw
    except FileNotFoundError:
        return _empty_state()
    except Exception as exc:
        logger.warning("Ignoring unreadable route health state %s: %s", path, exc)
        return _empty_state()


def _write_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, state, indent=2, mode=0o600)


@contextlib.contextmanager
def _state_file_lock():
    """Serialize route-health read/modify/write transactions across processes.

    Desktop, gateway, and CLI can all serve the same profile concurrently. The
    in-process RLock prevents thread races only; without a kernel file lock two
    processes can both read the same snapshot and overwrite each other's route
    update or both claim the same half-open probe lease.
    """
    from hermes_cli.auth import _file_lock

    path = state_path()
    lock_suffix = f"{path.suffix}.lock" if path.suffix else ".lock"
    lock_path = path.with_suffix(lock_suffix)
    with _file_lock(
        lock_path,
        _STATE_LOCK_HOLDER,
        _STATE_LOCK_TIMEOUT_S,
        "Timed out waiting for route health lock",
    ):
        yield


def _reason_value(reason: FailoverReason | None) -> str:
    return reason.value if isinstance(reason, FailoverReason) else FailoverReason.unknown.value


def _base_cooldown(reason: FailoverReason | None) -> int | None:
    """Cooldown only route-shaped failures; request/content-shape failures must fail open."""
    effective = reason or FailoverReason.unknown
    if effective not in _BASE_COOLDOWNS:
        return None
    cfg = _config()
    overrides = cfg.get("cooldown_seconds") or {}
    name = _reason_value(effective)
    if isinstance(overrides, dict) and name in overrides:
        try:
            return max(0, int(overrides[name]))
        except (TypeError, ValueError):
            pass
    return _BASE_COOLDOWNS[effective]


def record_failure(provider: str, model: str, base_url: str = "", reason: FailoverReason | None = None) -> int:
    """Persist a route failure and return its exponential cooldown in seconds."""
    if not enabled() or not provider or not model:
        return 0
    now = time.time()
    key, identity = route_identity(provider, model, base_url)
    base = _base_cooldown(reason)
    if base is None:
        return 0
    try:
        with _LOCK, _state_file_lock():
            state = _read_state()
            previous = state["routes"].get(key) or {}
            failures = max(0, int(previous.get("consecutive_failures") or 0)) + 1
            cooldown = min(base * (2 ** min(failures - 1, 8)), _MAX_COOLDOWN_S) if base else 0
            state["routes"][key] = {
                **identity,
                "status": "open" if cooldown else "healthy",
                "reason": _reason_value(reason),
                "consecutive_failures": failures,
                "cooldown_until": now + cooldown,
                "probe_until": 0,
                "last_failure_at": now,
                "last_success_at": previous.get("last_success_at"),
            }
            try:
                _write_state(state)
            except Exception as exc:
                logger.warning("Could not persist route failure health: %s", exc)
            return cooldown
    except Exception as exc:
        logger.warning("Could not lock route failure health; failing open: %s", exc)
        return 0


def allow_route(provider: str, model: str, base_url: str = "", *, claim_probe: bool = True) -> tuple[bool, int, str]:
    """Return ``(allowed, retry_after_seconds, state)``.

    Once an open route's cooldown expires, exactly one local caller claims a short
    half-open probe lease.  Other sessions keep using fallbacks until that probe either
    succeeds or its lease expires.  File errors fail open.
    """
    if not enabled() or not provider or not model:
        return True, 0, "disabled"
    now = time.time()
    key, identity = route_identity(provider, model, base_url)
    try:
        with _LOCK, _state_file_lock():
            state = _read_state()
            row = state["routes"].get(key)
            if not isinstance(row, dict):
                return True, 0, "healthy"
            cooldown_until = float(row.get("cooldown_until") or 0)
            if cooldown_until > now:
                return False, max(1, int(cooldown_until - now + 0.999)), "open"
            probe_until = float(row.get("probe_until") or 0)
            if probe_until > now:
                return False, max(1, int(probe_until - now + 0.999)), "half_open_busy"
            if not claim_probe or row.get("status") == "healthy":
                return True, 0, "healthy"
            lease = max(5, int(_config().get("probe_lease_seconds") or _DEFAULT_PROBE_LEASE_S))
            state["routes"][key] = {**row, **identity, "status": "half_open", "probe_until": now + lease}
            try:
                _write_state(state)
            except Exception as exc:
                logger.warning("Could not claim route health probe; failing open: %s", exc)
            return True, 0, "half_open_probe"
    except Exception as exc:
        logger.warning("Could not lock route health state; failing open: %s", exc)
        return True, 0, "lock_unavailable"


def record_success(provider: str, model: str, base_url: str = "") -> None:
    """Close a route circuit after an actual successful model response."""
    if not enabled() or not provider or not model:
        return
    now = time.time()
    key, identity = route_identity(provider, model, base_url)
    try:
        with _LOCK, _state_file_lock():
            state = _read_state()
            previous = state["routes"].get(key) or {}
            state["routes"][key] = {
                **identity,
                "status": "healthy",
                "reason": None,
                "consecutive_failures": 0,
                "cooldown_until": 0,
                "probe_until": 0,
                "last_failure_at": previous.get("last_failure_at"),
                "last_success_at": now,
            }
            try:
                _write_state(state)
            except Exception as exc:
                logger.warning("Could not persist route recovery health: %s", exc)
    except Exception as exc:
        logger.warning("Could not lock route recovery health; failing open: %s", exc)


def record_agent_success(agent) -> None:
    """Close the active route and finalize a verified primary failback, if any."""
    record_success(
        getattr(agent, "provider", ""), getattr(agent, "model", ""),
        str(getattr(agent, "base_url", "") or ""),
    )
    if not getattr(agent, "_primary_restore_probe_pending", False):
        return
    previous = getattr(agent, "_primary_restore_previous_route", None)
    if not (isinstance(previous, (list, tuple)) and len(previous) == 2):
        previous = ("unknown", "unknown")
    previous_model, previous_provider = (str(value or "unknown") for value in previous)
    agent._primary_restore_probe_pending = False
    agent._primary_restore_previous_route = None
    agent._provider_fallback_active = False
    agent._provider_fallback_route = None
    agent._rate_limit_backoff_count = 0
    agent._rate_limited_until = 0
    try:
        agent._emit_status(
            f"✅ Primary model recovered: {agent.model} via {agent.provider}; "
            f"fallback {previous_model} via {previous_provider} is no longer active."
        )
    except Exception:
        pass


def snapshot() -> dict[str, Any]:
    """Read-only operator snapshot used by status surfaces and tests."""
    with _LOCK:
        return _read_state()


def reset_for_tests() -> None:
    """Remove the active profile's health state; test-only convenience."""
    with _LOCK, contextlib.suppress(FileNotFoundError):
        state_path().unlink()
