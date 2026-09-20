"""Suggested cron jobs — proposed automations the user accepts with one tap.

A suggestion is a ready-to-run cron job spec the user accepts (creates the real job) or dismisses
(latched by ``dedup_key`` so it is never re-offered). Every proposal flows through here regardless
of source: ``catalog`` (curated starters), ``blueprint`` (skill ``blueprint:`` blocks, see
``tools/blueprints.py``), ``usage`` (self-improvement review), ``integration`` (connected account).
Accepting calls ``cron.jobs.create_job`` with the stored ``job_spec`` — no second job engine;
nothing auto-creates (consent-first). Storage mirrors ``cron/jobs.py`` (atomic replace, 0600).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes_time import now as _hermes_now
from utils import atomic_json_write

logger = logging.getLogger(__name__)

# Per-profile by design: resolve the path at CALL time so multiplexed profile scopes cannot
# leak one profile's suggestions into another. Optional explicit path is for isolated tests.
SUGGESTIONS_FILE: Optional[Path] = None

# Protects load->modify->save cycles (the background review fork and the main agent can both write).
_suggestions_lock = threading.RLock()

# Cap pending suggestions so the list never becomes a nag wall; when full, new ones are dropped.
MAX_PENDING = 5

VALID_SOURCES = frozenset({"catalog", "blueprint", "usage", "integration"})
_STATUS_PENDING = "pending"
_STATUS_ACCEPTED = "accepted"
_STATUS_DISMISSED = "dismissed"


def _current_suggestions_file() -> Path:
    return SUGGESTIONS_FILE or (get_hermes_home().resolve() / "cron" / "suggestions.json")


def _ensure_dir() -> None:
    from cron.jobs import _ensure_cron_dir

    _ensure_cron_dir(_current_suggestions_file().parent)


@contextlib.contextmanager
def _suggestions_mutation_lock():
    """Serialize suggestion decisions across threads and Hermes processes.

    Reuse cron.jobs' bounded, cross-platform flock primitives but keep a distinct lock file:
    suggestion acceptance may create a job (which takes .jobs.lock), so the two authorities must
    not alias and accidentally turn that nested create into self-contention.
    """
    with _suggestions_lock:
        _ensure_dir()
        from cron.jobs import _JOBS_LOCK_TIMEOUT_SECONDS, _acquire_flock, _release_flock

        lock_path = _current_suggestions_file().with_suffix(".lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "a+", encoding="utf-8")
            lock_fd.seek(0)
            acquired = _acquire_flock(lock_fd, _JOBS_LOCK_TIMEOUT_SECONDS)
        except Exception as exc:
            if lock_fd is not None:
                with contextlib.suppress(Exception):
                    lock_fd.close()
            raise RuntimeError(f"suggestion store lock unavailable: {lock_path}") from exc
        if acquired is False:
            lock_fd.close()
            raise RuntimeError(f"timed out waiting for suggestion store lock: {lock_path}")
        if acquired is None:
            lock_fd.close()
            raise RuntimeError(f"cross-process suggestion locking is unavailable: {lock_path}")
        try:
            yield
        finally:
            _release_flock(lock_fd)


def _load_raw() -> Dict[str, Any]:
    suggestions_file = _current_suggestions_file()
    if not suggestions_file.exists():
        return {"suggestions": []}
    try:
        with open(suggestions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("suggestions.json unreadable (%s); starting empty", e)
        return {"suggestions": []}
    if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
        return data
    if isinstance(data, list):
        return {"suggestions": data}
    logger.warning("suggestions.json malformed; starting empty")
    return {"suggestions": []}


def _save_raw(suggestions: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    payload = {"suggestions": suggestions, "updated_at": _hermes_now().isoformat()}
    atomic_json_write(_current_suggestions_file(), payload, mode=0o600)


def load_suggestions() -> List[Dict[str, Any]]:
    """Return all suggestion records (any status)."""
    return _load_raw().get("suggestions", [])


def _pending(suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [s for s in suggestions if s.get("status") == _STATUS_PENDING]


def list_pending() -> List[Dict[str, Any]]:
    """Return pending suggestions in creation order (oldest first)."""
    return _pending(load_suggestions())


def add_suggestion(
    *, title: str, description: str, source: str, job_spec: Dict[str, Any], dedup_key: str,
) -> Optional[Dict[str, Any]]:
    """Register a pending suggestion. Returns the record, or None when skipped: the same
    ``dedup_key`` was already decided on or is still pending (never re-offer, never duplicate), or
    the pending list is full (``MAX_PENDING``). ``job_spec`` is passed straight to
    ``cron.jobs.create_job`` on accept."""
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown suggestion source: {source!r}")
    if not title.strip() or not dedup_key.strip():
        raise ValueError("title and dedup_key are required")

    with _suggestions_mutation_lock():
        suggestions = _load_raw().get("suggestions", [])
        if any(
            existing.get("dedup_key") == dedup_key
            and existing.get("status") in (_STATUS_DISMISSED, _STATUS_ACCEPTED, _STATUS_PENDING)
            for existing in suggestions
        ):
            return None
        if len(_pending(suggestions)) >= MAX_PENDING:
            logger.info("Suggestion backlog full (%d); dropping %r", MAX_PENDING, title)
            return None

        record = {
            "id": uuid.uuid4().hex[:12],
            "title": title.strip(),
            "description": description.strip(),
            "source": source,
            "job_spec": job_spec,
            "dedup_key": dedup_key.strip(),
            "status": _STATUS_PENDING,
            "created_at": _hermes_now().isoformat(),
        }
        suggestions.append(record)
        _save_raw(suggestions)
        return record


def _resolve_suggestion(suggestions: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    for suggestion in suggestions:
        if suggestion.get("id") == ref:
            return suggestion
    if ref.isdigit():
        pending = _pending(suggestions)
        idx = int(ref) - 1
        if 0 <= idx < len(pending):
            return pending[idx]
    lowered = ref.lower()
    for suggestion in suggestions:
        if suggestion.get("title", "").lower() == lowered:
            return suggestion
    return None


def get_suggestion(ref: str) -> Optional[Dict[str, Any]]:
    """Resolve a suggestion by id, 1-based pending index, or exact (case-insensitive) title."""
    return _resolve_suggestion(load_suggestions(), ref)


def _resolve_in_place(
    suggestions: List[Dict[str, Any]], suggestion: Dict[str, Any], status: str,
) -> None:
    suggestion["status"] = status
    suggestion["resolved_at"] = _hermes_now().isoformat()
    _save_raw(suggestions)


def _set_status(suggestion_id: str, status: str) -> bool:
    with _suggestions_mutation_lock():
        suggestions = _load_raw().get("suggestions", [])
        suggestion = next((s for s in suggestions if s.get("id") == suggestion_id), None)
        if suggestion is None:
            return False
        _resolve_in_place(suggestions, suggestion, status)
        return True


def dismiss_suggestion(ref: str) -> bool:
    """Atomically dismiss a pending suggestion; resolved decisions are immutable."""
    with _suggestions_mutation_lock():
        suggestions = _load_raw().get("suggestions", [])
        suggestion = _resolve_suggestion(suggestions, ref)
        if not suggestion or suggestion.get("status") != _STATUS_PENDING:
            return False
        _resolve_in_place(suggestions, suggestion, _STATUS_DISMISSED)
        return True


def accept_suggestion(
    ref: str, *, origin: Optional[Dict[str, Any]] = None,
    local_session_origin: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Accept a suggestion and create its real cron job.

    ``origin`` carries messaging delivery. ``local_session_origin`` is accepted only after the
    caller has validated the durable Desktop/TUI session; turn-context callers keep using the
    shared capture helper. Nothing here invents a route from client-supplied identifiers.
    """
    from cron.scheduler import (
        CronSchedulerRegistrationError, create_job_with_scheduler_registration,
        register_persisted_job,
    )

    with _suggestions_mutation_lock():
        suggestions = _load_raw().get("suggestions", [])
        s = _resolve_suggestion(suggestions, ref)
        if not s or s.get("status") != _STATUS_PENDING:
            return None

        suggestion_id = str(s.get("id") or "").strip()
        if not suggestion_id:
            raise ValueError("suggestion id is required")

        # A process can die after jobs.json commits but before suggestions.json is resolved.
        # Reconcile that durable job first so retry never creates a second random job id.
        from cron.jobs import load_jobs
        existing = next(
            (
                job for job in load_jobs()
                if str(job.get("source_suggestion_id") or "").strip() == suggestion_id
            ),
            None,
        )
        if existing is not None:
            try:
                job = register_persisted_job(existing)
            except CronSchedulerRegistrationError:
                _resolve_in_place(suggestions, s, _STATUS_ACCEPTED)
                raise
            _resolve_in_place(suggestions, s, _STATUS_ACCEPTED)
            return job

        spec = dict(s.get("job_spec") or {})
        spec["source_suggestion_id"] = suggestion_id
        if origin is not None and "origin" not in spec:
            spec["origin"] = origin
        from cron.session_return import capture_local_session_origin, local_session_origin as normalize_local_origin
        if "local_session_origin" not in spec:
            local_origin = None
            if local_session_origin is not None:
                local_origin = normalize_local_origin(
                    spec.get("deliver"),
                    local_session_origin.get("source"),
                    local_session_origin.get("session_id"),
                )
            if local_origin is None:
                local_origin = capture_local_session_origin(spec.get("deliver"))
            if local_origin is not None:
                spec["local_session_origin"] = local_origin

        try:
            job = create_job_with_scheduler_registration(**spec)
        except CronSchedulerRegistrationError:
            # The job is already durable: resolve the suggestion so a retry cannot create a second copy.
            _resolve_in_place(suggestions, s, _STATUS_ACCEPTED)
            raise
        _resolve_in_place(suggestions, s, _STATUS_ACCEPTED)
        return job


def clear_resolved() -> int:
    """Drop ACCEPTED records from disk (they served their purpose once the job exists); dismissed
    records are RETAINED for their dedup_key. Returns the count removed."""
    with _suggestions_mutation_lock():
        suggestions = _load_raw().get("suggestions", [])
        kept = [s for s in suggestions if s.get("status") != _STATUS_ACCEPTED]
        removed = len(suggestions) - len(kept)
        if removed:
            _save_raw(kept)
        return removed
