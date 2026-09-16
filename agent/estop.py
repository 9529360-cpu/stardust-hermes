"""Global emergency stop (ESTOP) — a resumable pause for NEW work only.

``hermes pause`` writes a sentinel at ``$HERMES_HOME/ESTOP``; ``hermes resume``
removes it. While it exists the cron scheduler, kanban dispatcher and new gateway
turns skip work; in-flight work is never killed. The check is one or two uncached
``os.stat`` calls (process home + fleet root when they differ). The body is optional
JSON ``{"reason", "engaged_at"}``; a corrupt/empty file still counts as engaged
(fail safe, e.g. ``touch ~/.hermes/ESTOP``). Ported from gastownhall/gastown estop.go (MIT).
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Same profile-aware / fleet-root resolvers the file-safety guards use (fail-open to ~/.hermes).
from agent.file_safety import _hermes_home_path as _hermes_home, _hermes_root_path as _canonical_root

SENTINEL_NAME = "ESTOP"

# Per-component "logged already for this engagement" flags: log once per engagement, not per tick.
_log_lock = threading.Lock()
_logged_components: set[str] = set()


@dataclass(frozen=True)
class DisengageResult:
    """Authoritative outcome of one resume attempt.

    ``resumed`` is true only when at least one visible/fail-safe sentinel existed and every candidate
    is now verifiably absent.  ``incomplete`` covers unlink/stat failures and any sentinel that
    remains, so callers never have to infer safety from "we managed to unlink one file".
    """

    had_sentinel: bool
    removed_paths: tuple[Path, ...]
    failed_paths: tuple[Path, ...]
    remaining_paths: tuple[Path, ...]

    @property
    def resumed(self) -> bool:
        return self.had_sentinel and not self.failed_paths and not self.remaining_paths

    @property
    def incomplete(self) -> bool:
        return bool(self.failed_paths or self.remaining_paths)


def sentinel_path() -> Path:
    """Path of the ESTOP sentinel this process would write on `hermes pause`."""
    return _hermes_home() / SENTINEL_NAME


def _candidate_sentinel_paths() -> list:
    """Profile home first, then the fleet root if it is a different directory: a profile
    gateway (HERMES_HOME=~/.hermes/profiles/<n>) must still honor an operator's ~/.hermes/ESTOP."""
    primary = sentinel_path()
    try:
        root = _canonical_root() / SENTINEL_NAME
    except Exception:
        return [primary]
    try:
        distinct = root.resolve() != primary.resolve()
    except Exception:
        # Non-Path test doubles fail .resolve(); plain equality still dedupes.
        distinct = root != primary
    return [primary, root] if distinct else [primary]


def is_engaged() -> bool:
    """True if ANY candidate sentinel exists; fail SAFE (True) on stat errors."""
    saw_stat_error = False
    for path in _candidate_sentinel_paths():
        try:
            if path.exists():
                return True
        except OSError:
            saw_stat_error = True
    return saw_stat_error


def engage(reason: Optional[str] = None) -> Path:
    """Create the ESTOP sentinel. Idempotent; re-engaging updates the file."""
    path = sentinel_path()
    payload = {"engaged_at": datetime.now(timezone.utc).isoformat(), "reason": reason or None}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        with suppress(OSError):  # Best effort: an empty/partial sentinel still pauses (fail safe).
            path.touch(exist_ok=True)
    return path


def disengage_result() -> DisengageResult:
    """Remove every visible sentinel and verify that the emergency stop is actually clear.

    A stat error is treated exactly like :func:`is_engaged`: fail safe.  A concurrent remover that
    wins between ``exists`` and ``unlink`` is success, while permission/filesystem failures remain
    explicit.  A second verification pass closes the partial-delete hole where one profile/fleet
    sentinel disappeared but another could not be removed.
    """
    paths = _candidate_sentinel_paths()
    had_sentinel = False
    removed: list[Path] = []
    failed: list[Path] = []

    for path in paths:
        try:
            exists = path.exists()
        except (OSError, AttributeError):
            # is_engaged() treats an unreadable candidate as engaged; resume must be at least as safe.
            had_sentinel = True
            failed.append(path)
            continue
        if not exists:
            continue

        had_sentinel = True
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            # Another actor completed the same resume between exists() and unlink().
            removed.append(path)
        except (OSError, AttributeError):
            failed.append(path)

    remaining: list[Path] = []
    for path in paths:
        try:
            if path.exists():
                remaining.append(path)
        except (OSError, AttributeError):
            # Unknown is engaged/fail-safe. Preserve both the operation failure and the remaining
            # safety state without duplicating entries.
            if path not in failed:
                failed.append(path)
            remaining.append(path)

    return DisengageResult(
        had_sentinel=had_sentinel,
        removed_paths=tuple(removed),
        failed_paths=tuple(failed),
        remaining_paths=tuple(remaining),
    )


def disengage() -> bool:
    """Compatibility wrapper: True only when resume fully succeeded, False otherwise."""
    return disengage_result().resumed


def get_state() -> Optional[dict]:
    """Return ``{"reason", "engaged_at"}`` or None when not engaged; an unreadable/corrupt
    body still reports engaged with both fields None."""
    if not is_engaged():
        return None
    state = {"reason": None, "engaged_at": None}
    found = False
    for path in _candidate_sentinel_paths():
        try:
            if not path.exists():
                continue
        except OSError:
            return state
        except AttributeError:
            continue
        found = True
        with suppress(OSError, ValueError, AttributeError):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = {"reason": raw.get("reason") or None, "engaged_at": raw.get("engaged_at") or None}
                break
    return state if found else None


def paused_reply() -> Optional[str]:
    """Short user-facing notice for new gateway turns, or None if not paused."""
    state = get_state()
    if state is None:
        return None
    tag = f" ({state['reason']})" if state.get("reason") else ""
    return f"⏸️ Hermes is paused{tag}. New work is on hold; run `hermes resume` to pick things back up."


def check_paused(component: str, logger: logging.Logger) -> bool:
    """Return True when engaged, logging once per engagement per component (re-armed after a resume)."""
    if not is_engaged():
        with _log_lock:
            _logged_components.discard(component)
        return False
    with _log_lock:
        first = component not in _logged_components
        _logged_components.add(component)
    if first:
        reason = (get_state() or {}).get("reason")
        suffix = f" (reason: {reason})" if reason else ""
        logger.info(
            "%s dispatch paused by global emergency stop%s — remove with `hermes resume` (%s)",
            component, suffix, sentinel_path(),
        )
    return True


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
import os  # noqa: F401,E402
# ---- END PLUGIN-COMPAT ----
