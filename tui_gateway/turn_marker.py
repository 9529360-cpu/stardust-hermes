"""Durable interrupted-turn markers for the desktop/TUI auto-continue path. A running turn's progress
lives only in process memory (the agent flushes to SQLite at turn end), so a marker is written at turn
start and cleared on any conclusion — only a process death leaves one behind, and ``session.resume``
reads it (``_maybe_schedule_auto_continue``). Stored per ``HERMES_HOME`` (profile-aware); writes prune
entries older than ``_MAX_AGE_SECS`` and cap the count so a crash streak can't grow the file. Writers
hold a persistent sibling lockfile across load/mutate/atomic replace: a second backend process must not
overwrite the first process's newly recorded session with a stale snapshot. Every function is
best-effort — marker bookkeeping must never break a turn — so I/O errors degrade to "no marker"
instead of raising."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_MAX_AGE_SECS = 24 * 3600
_MAX_ENTRIES = 32
# Enough to re-submit any realistic prompt; guards against a multi-megabyte paste being journaled.
_MAX_PROMPT_CHARS = 64_000

_lock = threading.Lock()


@contextmanager
def _write_lock(path: Path):
    """Lock a stable sibling file: the JSON file itself is atomically replaced or unlinked."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _marker_path(home: Path | str) -> Path:
    return Path(home) / "desktop" / "interrupted_turns.json"


def _started_at(entry: dict) -> float:
    return float(entry.get("started_at") or 0)


def _load(path: Path) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.debug("unreadable turn-marker file %s; starting fresh", path, exc_info=True)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _prune(entries: dict[str, dict], now: float) -> dict[str, dict]:
    fresh = {k: e for k, e in entries.items() if now - _started_at(e) <= _MAX_AGE_SECS}
    if len(fresh) <= _MAX_ENTRIES:
        return fresh
    return dict(sorted(fresh.items(), key=lambda item: _started_at(item[1]), reverse=True)[:_MAX_ENTRIES])


def _store(path: Path, entries: dict[str, dict]) -> None:
    if not entries:
        path.unlink(missing_ok=True)
        return
    atomic_json_write(path, entries, indent=None, mode=0o600)


def _update(home: Path | str, session_key: str, mutate, what: str) -> None:
    """Load → ``mutate(entries)`` → store under the lock; ``mutate`` returns None to skip the write."""
    try:
        with _lock:
            path = _marker_path(home)
            with _write_lock(path):
                entries = mutate(_load(path))
                if entries is not None:
                    _store(path, entries)
    except Exception:
        logger.debug("failed to %s turn marker for %s", what, session_key, exc_info=True)


def record_turn_start(home: Path | str, session_key: str, prompt: str, *, attempts: int = 0,
                      auto_continue: bool = True) -> None:
    """Persist the marker for a turn that is about to run. ``attempts`` = how many auto-continues led to
    this run (0 for a user-initiated turn); the crash-loop breaker reads it back on the next resume."""
    if not session_key or not prompt:
        return
    now = time.time()
    entry = {"attempts": max(0, int(attempts)), "prompt": prompt[:_MAX_PROMPT_CHARS], "started_at": now,
             "auto_continue": bool(auto_continue)}
    _update(home, session_key, lambda entries: {**_prune(entries, now), session_key: entry}, "record")


def clear_turn_marker(home: Path | str, session_key: str) -> None:
    """Remove the marker once its turn concluded (any outcome the client saw)."""
    if session_key:
        _update(home, session_key, lambda e: {k: v for k, v in e.items() if k != session_key} if session_key in e else None, "clear")


def read_turn_marker(home: Path | str, session_key: str) -> dict[str, Any] | None:
    """The marker left by a turn that never concluded, or None."""
    if not session_key:
        return None
    try:
        with _lock:
            entry = _load(_marker_path(home)).get(session_key)
        prompt = str(entry.get("prompt") or "") if isinstance(entry, dict) else ""
        if not prompt.strip():
            return None
        return {"attempts": max(0, int(entry.get("attempts") or 0)), "prompt": prompt, "started_at": _started_at(entry),
                "auto_continue": bool(entry.get("auto_continue", True))}
    except Exception:
        return None
