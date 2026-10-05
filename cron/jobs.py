"""Cron job storage: ~/.hermes/cron/jobs.json; output in
~/.hermes/cron/output/{job_id}/{timestamp}.md

Schedule grammar, record edits, run claims, the due scan and ticker markers live in the
``cron.jobs_*`` siblings, re-exported at the bottom of this file; they reach this module
late-bound via ``_jobs``.
"""

import contextlib
import copy
import errno
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import logging
import shutil
import tempfile
import threading
import time
import os
import re
import uuid

# Cross-process advisory locking for jobs.json: fcntl (Unix) or msvcrt (Windows). If both are
# absent, _jobs_lock() degrades to in-process locking rather than failing.
try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hermes_constants import get_hermes_home
from cron.env_settings import cron_env_setting
from typing import Optional, Dict, List, Any, Callable, Set, Tuple, Union, Collection

logger = logging.getLogger(__name__)

from hermes_time import now as _hermes_now
from hermes_time import get_timezone
from utils import atomic_replace, atomic_write_text

# croniter is imported lazily (slow import, only needed for cron exprs). HAS_CRONITER stays a
# module attribute: a monkeypatched value wins because _ensure_croniter only probes while None.
croniter = None
HAS_CRONITER: Optional[bool] = None


def _ensure_croniter() -> bool:
    """Import croniter on first use; honor a pre-set HAS_CRONITER override."""
    global croniter, HAS_CRONITER
    if HAS_CRONITER is None:
        try:
            from croniter import croniter as _croniter
            croniter = _croniter
            HAS_CRONITER = True
        except ImportError:
            HAS_CRONITER = False
    return bool(HAS_CRONITER)


# --- Configuration ---

# Cron is per-profile by design: anchor at get_hermes_home() (active profile home), NOT
# get_default_hermes_root() — the shared root would funnel every profile's jobs into one jobs.json
# and run them under the ticker's HERMES_HOME, leaking config/credentials/skills across profiles.
# Each profile owns its own cron store under its own HERMES_HOME, and a profile-scoped gateway runs that
# profile's jobs under that same HERMES_HOME — so a job authored in profile `coder` lives in
# `~/.hermes/profiles/coder/cron/jobs.json` and executes with `coder`'s `.env`, `config.yaml`, and skills.
# Do NOT change this to the default root: that re-breaks per-profile isolation. See also the dynamic
# `_get_hermes_home()` / `_get_lock_paths()` resolution in cron/scheduler.py. See #4707.
HERMES_DIR = get_hermes_home().resolve()
# Default-profile fallback and compatibility surface for callers/tests. Cross-profile callers must
# scope paths with use_cron_store() instead of mutating these process-wide.
CRON_DIR = HERMES_DIR / "cron"
JOBS_FILE = CRON_DIR / "jobs.json"
# Heartbeat: touched every ticker loop so `hermes cron status` can tell the ticker THREAD is alive,
# not just the gateway PROCESS; success = last tick that completed WITHOUT raising.
# The gateway process and the (separate) ``hermes cron status`` process share it so status can tell whether
# the ticker THREAD is alive, not just whether the gateway PROCESS exists — a ticker that dies silently
# inside a live gateway would otherwise report healthy (#32612, #32895).
TICKER_HEARTBEAT_FILE = CRON_DIR / "ticker_heartbeat"
TICKER_SUCCESS_FILE = CRON_DIR / "ticker_last_success"
# Single source of truth for the ticker interval (scheduler_provider.py) and the staleness
# threshold in `hermes cron status` (hermes_cli/cron.py), so they never drift apart.
TICKER_INTERVAL_SECONDS = 60

# In-process lock for load_jobs→modify→save_jobs cycles; without it, parallel tick threads'
# mark_job_run / advance_next_run calls clobber each other.
_jobs_file_lock = threading.RLock()
_jobs_lock_state = threading.local()
_fire_fence_locks: Dict[str, threading.RLock] = {}
_fire_fence_locks_guard = threading.Lock()
_fire_fence_lock_state = threading.local()

# Upper bound on waiting for the cross-process .jobs.lock. Every cron function funnels through
# _jobs_lock(), so blocking forever on a wedged sibling process would freeze the ticker and every
# job. 30s is far above any legitimate critical section yet under one status-alarm threshold.
_JOBS_LOCK_TIMEOUT_SECONDS = 30.0
OUTPUT_DIR = CRON_DIR / "output"
ONESHOT_GRACE_SECONDS = 120


@dataclass(frozen=True)
class _CronStorePaths:
    cron_dir: Path
    jobs_file: Path
    output_dir: Path

    @classmethod
    def for_dir(cls, cron_dir: Path) -> "_CronStorePaths":
        return cls(cron_dir, cron_dir / "jobs.json", cron_dir / "output")


_cron_store_override: ContextVar[Optional[_CronStorePaths]] = ContextVar(
    "cron_store_override", default=None)


class _SelfRemovalDelivery:
    """Mutable run-local marker shared with the agent's copied ContextVar context."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.removed = False


_self_removal_delivery: ContextVar[Optional[_SelfRemovalDelivery]] = ContextVar(
    "self_removal_delivery", default=None)


@contextlib.contextmanager
def self_removal_delivery_scope(job_id: str):
    """Permit this run's final delivery after it removes its own job record."""
    marker = _SelfRemovalDelivery(job_id)
    token = _self_removal_delivery.set(marker)
    try:
        yield marker
    finally:
        _self_removal_delivery.reset(token)


def self_removal_delivery_allowed(job_id: str) -> bool:
    """Whether the active run deleted exactly its own job record and no record has since taken
    its id (a replacement record belongs to another owner, so that stays fail-closed)."""
    marker = _self_removal_delivery.get()
    if marker is None or marker.job_id != job_id or not marker.removed:
        return False
    return all(item.get("id") != job_id for item in load_jobs())

# Import-time snapshot so deliberate re-pointing of CRON_DIR/JOBS_FILE/OUTPUT_DIR (the documented
# escape hatch for tests/embedders) is distinguishable from the constants merely being stale.
_IMPORT_STORE = _CronStorePaths(CRON_DIR, JOBS_FILE, OUTPUT_DIR)


def _current_cron_store() -> _CronStorePaths:
    """Paths pinned to this execution context's profile. Precedence: (1) active use_cron_store()
    override; (2) deliberately re-pointed module constants; (3) the ACTIVE profile home via
    get_hermes_home(), so re-pointing HERMES_HOME after import uses ITS OWN store rather than the
    user's real jobs.json frozen at import; (4) import-time constants."""
    override = _cron_store_override.get()
    if override is not None:
        return override
    live_constants = _CronStorePaths(CRON_DIR, JOBS_FILE, OUTPUT_DIR)
    if live_constants != _IMPORT_STORE:
        return live_constants
    home = get_hermes_home().resolve()
    if home == HERMES_DIR:
        return live_constants
    return _CronStorePaths.for_dir(home / "cron")


@contextlib.contextmanager
def use_cron_store(home: Union[str, Path]):
    """Route cron storage to ``home`` without mutating process globals."""
    token = _cron_store_override.set(
        _CronStorePaths.for_dir(Path(home).expanduser().resolve() / "cron"))
    try:
        yield
    finally:
        _cron_store_override.reset(token)


def get_cron_output_dir() -> Path:
    """Return the output directory for the active cron store context."""
    return _current_cron_store().output_dir


# Fallback stale-recovery window for a one-shot's running-claim when HERMES_CRON_TIMEOUT=0
# (unlimited, no bound to derive from); also the floor so a tiny timeout can't expire a claim
# mid-run.
ONESHOT_RUN_CLAIM_TTL_SECONDS = 1800

# Derived TTL = inactivity timeout × this headroom. The TTL only recovers a claim left by a tick
# that DIED mid-run; the timeout is an *inactivity* limit, not a wall-clock cap, so healthy runs may
# legitimately exceed it — hence the headroom.
_ONESHOT_RUN_CLAIM_TTL_HEADROOM = 3

_DEFAULT_CRON_INACTIVITY_TIMEOUT = 600.0


def _oneshot_run_claim_ttl_seconds() -> float:
    """One-shot running-claim TTL from ``HERMES_CRON_TIMEOUT``: unset/invalid → 600s → 1800s;
    ``0`` (unlimited) → the fixed floor; positive N → ``max(N * headroom, floor)``."""
    raw = cron_env_setting("HERMES_CRON_TIMEOUT").strip()
    try:
        timeout = float(raw) if raw else _DEFAULT_CRON_INACTIVITY_TIMEOUT
    except (ValueError, TypeError):
        timeout = _DEFAULT_CRON_INACTIVITY_TIMEOUT
    if timeout <= 0:
        return float(ONESHOT_RUN_CLAIM_TTL_SECONDS)
    return max(timeout * _ONESHOT_RUN_CLAIM_TTL_HEADROOM, float(ONESHOT_RUN_CLAIM_TTL_SECONDS))


def _job_running_in_this_process(job_id: str) -> bool:
    """True when the scheduler in THIS process is still running ``job_id``: the run_claim TTL alone
    cannot distinguish "claiming tick died" from "alive but slow". Lazy import: scheduler imports
    us.

    Direct liveness signal for stale-entry recovery (#62002): the run_claim TTL alone cannot distinguish
    "the claiming tick died" from "the run is alive but slow" — a run stalled on network I/O (or a laptop
    that slept mid-run) legitimately outlives the TTL. The in-process ticker and the run share this process,
    so the scheduler's running set settles the common single-gateway case without any claim-age guesswork.
    """
    try:
        from cron.scheduler import get_running_job_ids
        return job_id in get_running_job_ids()
    except Exception:
        logger.warning(
            "Cron running-set liveness check failed for job %r; keeping the "
            "entry to avoid deleting a possibly live one-shot run",
            job_id, exc_info=True)
        return True


def _jobs_lock_file() -> Path:
    """Return the advisory lock path for the current cron directory."""
    return _current_cron_store().cron_dir / ".jobs.lock"


def _lock_contention_error(exc: OSError) -> bool:
    """Whether a non-blocking advisory-lock error means a peer owns the lock."""
    if exc.errno is None:
        return False
    if fcntl is not None:
        return exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
    if msvcrt is not None:
        return exc.errno in (errno.EACCES, errno.EDEADLK)
    return False


def _acquire_flock(lock_fd, timeout: float) -> Optional[bool]:
    """Bounded exclusive lock: True when acquired, False on timeout, None when no backend exists.

    Both POSIX and Windows use a non-blocking lock attempt against the same deadline. A blocking
    lock under the in-process lock would let a wedged sibling freeze every cron function forever.
    """
    if fcntl is None and msvcrt is None:
        return None

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                getattr(msvcrt, "locking")(
                    lock_fd.fileno(), getattr(msvcrt, "LK_NBLCK"), 1
                )
            return True
        except (OSError, IOError) as exc:
            if not _lock_contention_error(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))


def _release_flock(lock_fd) -> None:
    """Unlock (best effort) and close a lock file opened for ``_acquire_flock``."""
    try:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            getattr(msvcrt, "locking")(lock_fd.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
    except (OSError, IOError):
        pass
    finally:
        lock_fd.close()


@contextlib.contextmanager
def _jobs_lock():
    """Serialize a load_jobs→modify→save_jobs critical section: in-process RLock (parallel tick
    threads) plus a cross-process flock on ``<cron dir>/.jobs.lock`` (gateway vs. CLI writes —
    otherwise a `cron pause` could be clobbered and keep firing). Nested calls in one thread
    reuse the held lock. Without a flock backend, or on flock timeout (logged loudly), it
    degrades to in-process-only locking: a briefly torn cross-process write beats a dead
    scheduler."""
    depth = getattr(_jobs_lock_state, "depth", 0)
    if depth:
        _jobs_lock_state.depth = depth + 1
        try:
            yield
        finally:
            _jobs_lock_state.depth -= 1
        return

    with _jobs_file_lock:
        _jobs_lock_state.depth = 1
        # jobs.json stamp as of this section's load_jobs(): lets _save_jobs_unlocked skip the
        # shrink-merge parse when the file provably hasn't changed. Reset on entry/exit so stale
        # stamps from unlocked loads or prior sections can never suppress a needed merge.
        # See #80703.
        _jobs_lock_state.load_stamp = None
        lock_fd = None
        try:
            try:
                ensure_dirs()
                lock_fd = open(_jobs_lock_file(), "a+", encoding="utf-8")
                lock_fd.seek(0)
                if _acquire_flock(lock_fd, _JOBS_LOCK_TIMEOUT_SECONDS) is False:
                    logger.error(
                        "Timed out after %.0fs waiting for the cron "
                        "jobs lock (%s) — another process is holding "
                        "it. Proceeding with in-process locking only "
                        "so the scheduler stays alive (#60703).",
                        _JOBS_LOCK_TIMEOUT_SECONDS, _jobs_lock_file())
                    with contextlib.suppress(OSError):
                        lock_fd.close()
                    lock_fd = None
            except (OSError, IOError) as e:
                # A locking failure must never take down cron writes — in-process lock still held.
                logger.warning("jobs.json cross-process lock unavailable (%s); "
                               "proceeding with in-process lock only", e)
            try:
                yield
            finally:
                if lock_fd is not None:
                    _release_flock(lock_fd)
        finally:
            _jobs_lock_state.depth = 0
            _jobs_lock_state.load_stamp = None


@contextlib.contextmanager
def _fire_job_lock(job_id: str):
    """Serialize one job's owner mutations and external side effects. Unlike the global jobs lock
    this may be held across network delivery; scoped to one profile + job so unrelated jobs keep
    progressing. Fails closed when cross-process locking is unavailable."""
    cron_dir = _current_cron_store().cron_dir
    lock_key = f"{cron_dir.resolve()}::{job_id}"
    with _fire_fence_locks_guard:
        local_lock = _fire_fence_locks.setdefault(lock_key, threading.RLock())

    if not local_lock.acquire(timeout=_JOBS_LOCK_TIMEOUT_SECONDS):
        logger.error("Timed out waiting for local fire fence %s; failing closed", lock_key)
        yield False
        return

    held_locks = _fire_fence_lock_state.__dict__.setdefault("held", {})
    if lock_key in held_locks:
        try:
            yield held_locks[lock_key]
        finally:
            local_lock.release()
        return

    try:
        ensure_dirs()
        lock_path = cron_dir / f".fire-{uuid.uuid5(uuid.NAMESPACE_URL, lock_key).hex}.lock"
        lock_fd = None
        acquired = False
        try:
            lock_fd = open(lock_path, "a+", encoding="utf-8")
            lock_fd.seek(0)
            result = _acquire_flock(lock_fd, _JOBS_LOCK_TIMEOUT_SECONDS)
            if result is None:  # pragma: no cover - supported platforms provide one backend
                logger.error("No cross-process lock backend for cron fire fence")
            elif not result:
                logger.error("Timed out waiting for fire fence %s; failing closed", lock_path)
            acquired = bool(result)
        except (OSError, IOError) as exc:
            logger.error("Cron fire fence unavailable for %s: %s", job_id, exc)

        held_locks[lock_key] = acquired
        try:
            yield acquired
        finally:
            held_locks.pop(lock_key, None)
            if lock_fd is not None:
                if acquired:
                    _release_flock(lock_fd)
                else:
                    lock_fd.close()
    finally:
        local_lock.release()


def _under_fire_fence(job_id: str, fn: Callable[[], Any]) -> Any:
    """Run ``fn()`` holding the job's fire fence; False (fail closed) when it can't be acquired."""
    with _fire_job_lock(job_id) as acquired:
        if not acquired:
            return False
        return fn()


@contextlib.contextmanager
def fire_claim_fence(job_id: str, *, expected_owner: str):
    """Hold a per-job fence while an owner performs an external side effect. A missing record
    is accepted only for the active run that removed this exact job (#111039)."""
    with _fire_job_lock(job_id) as acquired:
        if not acquired:
            yield False
            return
        with _jobs_lock():
            job = next((item for item in load_jobs() if item.get("id") == job_id), None)
            claim = job.get("fire_claim") if isinstance(job, dict) else None
            owns_claim = isinstance(claim, dict) and claim.get("by") == expected_owner
            if job is None:
                owns_claim = self_removal_delivery_allowed(job_id)
        yield owns_claim


# Fields that must never change after creation: ``id`` is a path component under OUTPUT_DIR, so an
# update could leak ``../escape``/absolute/nested values into output writes/deletes.
_IMMUTABLE_JOB_FIELDS = frozenset({"id"})


def _job_output_dir(job_id: str) -> Path:
    """Resolve a job's output directory, rejecting any path-escape attempt (``..``, absolute
    paths, separators): only a single safe path component is accepted."""
    text = str(job_id or "").strip()
    if (
        not text or text in {".", ".."} or "/" in text or "\\" in text
        or Path(text).is_absolute() or Path(text).drive
    ):
        raise ValueError(f"Invalid cron job id for output path: {job_id!r}")
    return _current_cron_store().output_dir / text


def _normalize_skill_list(skill: Optional[str] = None, skills: Optional[Any] = None) -> List[str]:
    """Normalize legacy/single-skill and multi-skill inputs into a unique ordered list."""
    if skills is None:
        raw_items = [skill] if skill else []
    elif isinstance(skills, str):
        raw_items = [skills]
    else:
        raw_items = list(skills)
    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _apply_skill_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    """Return a job dict with canonical `skills` and legacy `skill` fields aligned."""
    normalized = dict(job)
    skills = _normalize_skill_list(normalized.get("skill"), normalized.get("skills"))
    normalized["skills"] = skills
    normalized["skill"] = skills[0] if skills else None
    return normalized


def _coerce_job_text(value: Any, fallback: str = "") -> str:
    """Coerce legacy/hand-edited nullable cron fields to strings for readers."""
    return fallback if value is None else str(value)


# Fields whose presence in an update can turn a runnable job into an empty one.
_PAYLOAD_FIELDS = frozenset({"prompt", "script", "skill", "skills", "no_agent"})

EMPTY_PAYLOAD_ERROR = (
    "Cron job has nothing to run: the prompt is blank and no script or "
    "skill(s) are set. Provide a prompt, a script, or at least one skill."
)

NO_AGENT_WITHOUT_SCRIPT_ERROR = (
    "no_agent=True requires a script — with no agent and no script "
    "there is nothing for the job to run."
)


def job_payload_is_empty(job: Dict[str, Any]) -> bool:
    """True when a job record has nothing runnable (blank prompt, no script, no skills) AND at
    least one payload field is explicitly present. ``no_agent`` already requires a script."""
    if _coerce_job_text(job.get("prompt")).strip() or _coerce_job_text(job.get("script")).strip():
        return False
    if _normalize_skill_list(job.get("skill"), job.get("skills")):
        return False
    return any(k in job for k in ("prompt", "script", "skill", "skills"))


def _schedule_display_for_job(job: Dict[str, Any]) -> str:
    display = _coerce_job_text(job.get("schedule_display")).strip()
    if display:
        return display
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        for key in ("display", "value", "expr", "run_at"):
            text = _coerce_job_text(schedule.get(key)).strip()
            if text:
                return text
    elif schedule is not None:
        return str(schedule)
    return "?"


def _normalize_job_record(job: Dict[str, Any]) -> Dict[str, Any]:
    """Read-safe job shape: legacy/hand-edited records may have nullable ``prompt``, ``name``,
    ``schedule_display``. Storage is untouched; consumers never crash on formatting."""
    normalized = _apply_skill_fields(job)
    job_id = normalized["id"] = _coerce_job_text(normalized.get("id"), "unknown")
    prompt = normalized["prompt"] = _coerce_job_text(normalized.get("prompt"))
    name = _coerce_job_text(normalized.get("name")).strip()
    if not name:
        label_source = (
            prompt
            or (normalized["skills"][0] if normalized.get("skills") else "")
            or _coerce_job_text(normalized.get("script")).strip()
            or job_id
            or "cron job"
        )
        name = label_source[:50].strip() or "cron job"
    normalized["name"] = name
    normalized["schedule_display"] = _schedule_display_for_job(normalized)
    # Derived from the scheduler-honoured ``enabled`` flag so a half-paused record cannot render
    # "paused" while still firing. See effective_job_state().
    normalized["state"] = effective_job_state(normalized)
    return normalized


def _has_pause_marker(job: Dict[str, Any]) -> bool:
    """True when the record carries any operator-facing pause signal."""
    return _coerce_job_text(job.get("state")).strip() == "paused" or bool(job.get("paused_at"))


def is_job_runnable(job: Dict[str, Any]) -> bool:
    """True iff the scheduler may fire this job: ``enabled`` plus pause markers as a second gate so
    a contradictory half-paused record never fires even before self-heal runs."""
    return bool(job.get("enabled", True)) and not _has_pause_marker(job)


def effective_job_state(job: Dict[str, Any]) -> str:
    """Operator-facing state derived from ``enabled``: an enabled job must never display as paused
    (list looked frozen while jobs kept firing). Terminal states are preserved regardless."""
    stored = _coerce_job_text(job.get("state")).strip()
    if stored in {"completed", "error"}:
        return stored
    if not job.get("enabled", True):
        if _has_pause_marker(job) or stored == "paused":
            return "paused"
        return stored or "paused"
    # enabled=true is authoritative: never claim paused
    if stored == "paused" or job.get("paused_at"):
        return "scheduled"
    return stored or "scheduled"


def is_terminal_job(job: Dict[str, Any]) -> bool:
    """Return whether a job record is in a terminal scheduler state."""
    return job.get("state") in {"completed", "error"}


def _is_recoverable_error_job(job: Dict[str, Any]) -> bool:
    """True for a recurring job stuck in ``state=error`` (set ONLY when ``compute_next_run()`` fails
    for a cron/interval job: croniter missing, malformed schedule). Such a job still has future
    occurrences once the issue resolves, so treating it as terminal would block due-scan self-heal,
    pre-advance, dispatch claim and ``resume_job`` — wedging it forever.

    Unlike ``state=completed`` (a one-shot that genuinely has no more occurrences, ever), an error-state
    recurring job still has a schedule with future occurrences once the underlying issue resolves — it is
    stuck pending a ``next_run_at`` recompute, not truly done. See #16265.
    """
    return (
        job.get("state") == "error"
        and (job.get("schedule") or {}).get("kind") in {"cron", "interval"}
    )


def _secure_dir(path: Path):
    """Owner-only (0700) via the shared helper, so cron/ and cron/output honor the same managed/
    container/HERMES_HOME_MODE rules as the rest of HERMES_HOME (#10757)."""
    from hermes_cli.config import _secure_dir as _shared_secure_dir
    _shared_secure_dir(path)


def _secure_file(path: Path):
    """Owner-only (0600) via the shared helper (managed/container skip included)."""
    from hermes_cli.config import _secure_file as _shared_secure_file
    _shared_secure_file(path)


def _preserve_file_ownership(path: Path, before: Optional[os.stat_result]) -> None:
    """Restore a rewritten file's previous owner (POSIX, root writer only): atomic replace makes the
    file owned by the writer's euid, so a root CLI write (e.g. ``docker exec``) against the
    unprivileged gateway's store would flip jobs.json to root:root 0600 and lock the ticker out."""
    if before is None or os.name != "posix":
        return
    geteuid = getattr(os, "geteuid", None)
    getegid = getattr(os, "getegid", None)
    if geteuid is None or getegid is None:
        return
    try:
        euid = geteuid()
        if euid != 0 or (before.st_uid, before.st_gid) == (euid, getegid()):
            return  # unprivileged writer, or already ours before the rewrite
        os.chown(path, before.st_uid, before.st_gid)
    except OSError as e:
        logger.warning(
            "Could not restore ownership of %s to uid=%s gid=%s after rewrite: %s "
            "— if the gateway runs as a different user, its cron ticker may now "
            "be locked out (see issue #68483).",
            path, before.st_uid, before.st_gid, e)


def _is_named_profile_path(path: Path) -> bool:
    """True if *path* is under ``<hermes_home>/profiles/<name>/`` (default/custom homes are not).
    Checks the resolved path (symlinked parents) and the raw path (symlinked profile homes)."""
    with contextlib.suppress(OSError, RuntimeError):
        if "profiles" in path.resolve().parts:
            return True
    return "profiles" in path.parts


def _ensure_cron_dir(cron_dir: Path) -> None:
    """Create a cron directory without resurrecting a deleted profile home: a stale multiplex
    scheduler may still hold a deleted profile's path, so named profiles use ``parents=False`` and
    fail closed. Default/custom homes keep ``parents=True`` so first-run creation works."""
    if _is_named_profile_path(cron_dir):
        cron_dir.mkdir(exist_ok=True)
        return
    cron_dir.mkdir(parents=True, exist_ok=True)


def ensure_dirs():
    """Ensure cron directories exist with secure permissions."""
    store = _current_cron_store()
    _ensure_cron_dir(store.cron_dir)
    _ensure_cron_dir(store.output_dir)
    _secure_dir(store.cron_dir)
    _secure_dir(store.output_dir)


# Recovery counter for recurring jobs wedged in stale ``last_status == "error"`` with a future
# next_run_at.
_persisted_error_recoveries: int = 0
# Bounded in-memory history kept by every probe-visible fire-path counter.
_TELEMETRY_RECENT_HISTORY = 20
_persisted_error_recoveries_recent: list = []


def _job_is_stale_error_recurring(
    job: Dict[str, Any], schedule: Dict[str, Any], now: datetime,
) -> bool:
    """True when a recurring job (caller-checked) is wedged in a stale persisted error state:
    ``last_status == "error"``, not running in this process (never re-arm a live run underneath
    itself), and ``last_run_at`` older than ``cadence + grace`` (a job merely erroring-and-retrying
    on schedule stays fresh and is not flagged).

    Condition (all must hold): * it has NOT successfully re-fired within its natural cadence — its
    ``last_run_at`` is older than ``cadence + grace``, so this is not a normal transient-error retry that
    will fire on its own soon, it is a job that has been sitting errored for a full period with no recovery;
    See #62002.
    """
    if job.get("last_status") != "error":
        return False
    if _job_running_in_this_process(str(job.get("id") or "")):
        return False
    # A fresh fire_claim means the job is running in ANOTHER process sharing this
    # store, not wedged; re-arming it here would only claim-fight the live run.
    if _claim_is_live(job.get("fire_claim"), now, FIRE_CLAIM_TTL_SECONDS):
        return False
    last_run = job.get("last_run_at")
    last_run_dt = _parse_aware(last_run) if last_run else None
    if last_run_dt is None:
        return False
    age_seconds = (now - last_run_dt).total_seconds()
    if age_seconds < 0:
        return False
    grace = _compute_grace_seconds(schedule)
    cadence_seconds = _schedule_cadence_seconds(schedule)
    if cadence_seconds is None:
        # Unknown cadence: fall back to the grace window, never re-arming anything younger than it.
        cadence_seconds = grace
    return age_seconds > (cadence_seconds + grace)


def _append_telemetry_record(filename: str, entry: Dict[str, Any], recent: list) -> None:
    """Record ``entry`` in the bounded ``recent`` list and append to ``<cron_dir>/<filename>``
    (best effort — telemetry must never break a tick). Counters stay module-level ints per
    metric because tests reset them by name."""
    recent.append(entry)
    del recent[:-_TELEMETRY_RECENT_HISTORY]
    try:
        path = _current_cron_store().cron_dir / filename
        _ensure_cron_dir(path.parent)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("Could not append %s record: %s", filename, exc)


def _record_persisted_error_recovery(job: Dict[str, Any], previous_next_run: str) -> None:
    """Persist a countable, probe-visible signal for one stale-error re-arm."""
    global _persisted_error_recoveries
    entry = {
        "job_id": job.get("id"),
        "name": job.get("name") or job.get("id"),
        "previous_next_run_at": previous_next_run,
        "rearmed_at": _hermes_now().isoformat(),
    }
    _persisted_error_recoveries += 1
    _append_telemetry_record(
        "persisted_error_recoveries.jsonl", entry, _persisted_error_recoveries_recent)


def get_persisted_error_recovery_stats() -> Dict[str, Any]:
    """Probe-visible snapshot of persisted-error recoveries."""
    return {
        "persisted_error_recoveries": _persisted_error_recoveries,
        "recent": list(_persisted_error_recoveries_recent),
    }


# Offset-migration catch-ups on the fire path: climbing after a deploy = draining; steady = TZ
# churn.
_timezone_migration_catchups: int = 0
_timezone_migration_catchups_recent: list = []


def _record_timezone_migration_catchup(
    job: Dict[str, Any], raw_next_run_dt: datetime, next_run_dt: datetime,
) -> None:
    """Persist a countable signal for one offset-migration catch-up fire."""
    global _timezone_migration_catchups
    entry = {
        "job_id": job.get("id"),
        "name": job.get("name") or job.get("id"),
        "expr": (job.get("schedule") or {}).get("expr"),
        "stored_next_run_at": raw_next_run_dt.isoformat(),
        "normalized_next_run_at": next_run_dt.isoformat(),
        "fired_at": _hermes_now().isoformat(),
    }
    _timezone_migration_catchups += 1
    _append_telemetry_record(
        "timezone_migration_catchups.jsonl", entry, _timezone_migration_catchups_recent)


def get_timezone_migration_catchup_stats() -> Dict[str, Any]:
    """Probe-visible snapshot of offset-migration catch-up fires."""
    return {
        "timezone_migration_catchups": _timezone_migration_catchups,
        "recent": list(_timezone_migration_catchups_recent),
    }


# --- Job CRUD Operations ---

def _parse_jobs_file(jobs_file: Path) -> Tuple[Any, bool]:
    """Tolerant jobs.json parse -> ``(data, used_strict_fallback)``: utf-8-sig absorbs a BOM, strict
    failure retries with ``strict=False``. IO/fallback errors propagate (caller decides repair vs
    bail)."""
    with open(jobs_file, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    try:
        return json.loads(raw), False
    except json.JSONDecodeError:
        return json.loads(raw, strict=False), True


def load_jobs() -> List[Dict[str, Any]]:
    """Load all jobs from storage."""
    jobs_file = _current_cron_store().jobs_file
    ensure_dirs()
    # Stamp BEFORE reading (fail-safe, see _record_load_stamp): a racing write then forces the
    # merge.
    pre_read_stamp = _jobs_file_stamp(jobs_file)
    if not jobs_file.exists():
        _record_load_stamp(None)
        return []

    try:
        data, _strict_retry = _parse_jobs_file(jobs_file)
    except IOError as e:
        logger.error("IOError reading jobs.json: %s", e)
        raise RuntimeError(f"Failed to read cron database: {e}") from e
    except Exception as e:
        logger.error("Failed to auto-repair jobs.json: %s", e)
        raise RuntimeError(f"Cron database corrupted and unrepairable: {e}") from e

    # Accept the canonical dict, or a bare list (auto-repair); any other top-level shape is
    # corruption.
    repair = "had invalid control characters" if _strict_retry else None
    if isinstance(data, dict):
        jobs = data.get("jobs", [])
        if isinstance(jobs, dict):
            # ID-keyed map from external tools: flatten (inline "id" wins, else the key), skip junk.
            # _peek_jobs_unlocked deliberately does NOT flatten, so saves never merge against it.
            skipped = [k for k, v in jobs.items() if not isinstance(v, dict)]
            if skipped:
                logger.warning(
                    "Skipping %d non-dict entr%s in id-keyed jobs map: %s",
                    len(skipped), "y" if len(skipped) == 1 else "ies",
                    ", ".join(map(repr, skipped)))
            jobs = [{**v, "id": v.get("id") or k} for k, v in jobs.items() if isinstance(v, dict)]
            repair = "id-keyed jobs map flattened to list"
    elif isinstance(data, list):
        jobs = data
        repair = "bare list wrapped as dict"
    else:
        raise RuntimeError(
            f"Cron database corrupted: expected {{'jobs': [...]}}, got {type(data).__name__}")
    if jobs and repair:
        save_jobs(jobs)
        logger.warning("Auto-repaired jobs.json (%s)", repair)
    _record_load_stamp(pre_read_stamp)
    return jobs


def _peek_jobs_unlocked() -> Optional[List[Dict[str, Any]]]:
    """Repair-free read under ``_jobs_lock()``: ``[]`` if missing, ``None`` if corrupt (never
    shrink-merge against an unknown baseline). Never saves — that would recurse."""
    jobs_file = _current_cron_store().jobs_file
    if not jobs_file.exists():
        return []
    try:
        data, _ = _parse_jobs_file(jobs_file)
    except Exception:
        return None
    if isinstance(data, dict):
        jobs = data.get("jobs", [])
        return jobs if isinstance(jobs, list) else None
    return data if isinstance(data, list) else None


def _jobs_file_stamp(jobs_file: Path) -> Optional[Tuple[int, int, int]]:
    """Shrink-merge fast-path stamp ``(mtime_ns, size, ino)``; ``None`` if unstatable. ``st_ino`` is
    included because every writer uses mkstemp+rename, so a same-size write in one mtime quantum
    can't false-match."""
    try:
        st = jobs_file.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def _record_load_stamp(stamp: Optional[Tuple[int, int, int]]) -> None:
    """Remember jobs.json's stamp for the enclosing _jobs_lock() section (no-op outside one) so the
    save path can skip the shrink-merge when disk provably hasn't changed. Capture it BEFORE
    reading: a mid-read sibling then mismatches (fail-safe); stamping after would certify an
    unseen write.

    Stamping after the read would let that sibling's write be certified as "seen" without being in the
    loaded payload, wrongly suppressing the recovery. See #80703.
    """
    if getattr(_jobs_lock_state, "depth", 0):
        _jobs_lock_state.load_stamp = stamp


def _unmerged_disk_jobs(
    jobs: List[Dict[str, Any]], removed_ids: Optional[Collection[str]]
) -> List[Dict[str, Any]]:
    """On-disk jobs missing from *jobs* and not intentionally removed. Stamp match => nothing
    landed, return without parsing; unreadable store => ``[]`` (never merge against an unknown
    baseline)."""
    stamp = getattr(_jobs_lock_state, "load_stamp", None)
    if stamp is not None and _jobs_file_stamp(_current_cron_store().jobs_file) == stamp:
        return []
    disk_jobs = _peek_jobs_unlocked()
    if disk_jobs is None:
        return []
    seen = {str(j["id"]) for j in jobs if isinstance(j, dict) and j.get("id")}
    seen |= {str(i) for i in (removed_ids or ()) if i}
    recovered: List[Dict[str, Any]] = []
    for disk_job in disk_jobs:
        if not isinstance(disk_job, dict) or not disk_job.get("id"):
            continue
        disk_id = str(disk_job["id"])
        if disk_id not in seen:
            recovered.append(disk_job)
            seen.add(disk_id)
    return recovered


def _merge_unexpected_disk_jobs(
    jobs: List[Dict[str, Any]], *, removed_ids: Optional[Collection[str]] = None,
) -> List[Dict[str, Any]]:
    """*jobs* plus on-disk jobs absent from the payload (under the degraded flock-timeout path a
    stale writer would otherwise clobber concurrent creates). Deletes pass ``removed_ids``; never
    mutates *jobs*."""
    recovered = _unmerged_disk_jobs(jobs, removed_ids)
    if not recovered:
        return jobs
    logger.warning(
        "Preserved %d cron job(s) present on disk but missing from the "
        "in-memory save payload (concurrent create under degraded lock "
        "or stale writer) (#80624): %s",
        len(recovered), [j.get("id") for j in recovered])
    return jobs + recovered


def _unlink_quiet(path: Optional[str]) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _stage_jobs_payload(jobs_file: Path, jobs: List[Dict[str, Any]]) -> str:
    """Serialize the store payload to a fsynced temp file next to *jobs_file*; return its path."""
    fd, tmp_path = tempfile.mkstemp(dir=str(jobs_file.parent), suffix=".tmp", prefix=".jobs_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {"jobs": jobs, "updated_at": _hermes_now().isoformat()},
                f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _unlink_quiet(tmp_path)
        raise
    return tmp_path


_SAVE_JOBS_MERGE_ATTEMPTS = 5


def _save_jobs_unlocked(
    jobs: List[Dict[str, Any]], *, removed_ids: Optional[Collection[str]] = None,
    replace: bool = False,
):
    """Save all jobs; caller must hold _jobs_lock(). ``removed_ids`` = intentional deletes;
    ``replace=True`` skips the shrink-merge guard (wholesale rewrite for tests/disaster
    recovery)."""
    jobs_file = _current_cron_store().jobs_file
    ensure_dirs()
    # Owner snapshot BEFORE replace so a root writer can hand the file back to the gateway user.
    _stat_before = None
    for probe in (jobs_file, jobs_file.parent):
        with contextlib.suppress(OSError):
            _stat_before = os.stat(probe)
            break

    # Shrink-merge loop: merge, stage, re-peek, repeat; the last attempt writes without a re-peek.
    tmp_path = None
    try:
        for attempt in range(_SAVE_JOBS_MERGE_ATTEMPTS + 1):
            if not replace:
                jobs = _merge_unexpected_disk_jobs(jobs, removed_ids=removed_ids)
            tmp_path = _stage_jobs_payload(jobs_file, jobs)
            # Verify-after-stage: a sibling landing during serialization forces another merge round.
            if (
                not replace
                and attempt < _SAVE_JOBS_MERGE_ATTEMPTS
                and _unmerged_disk_jobs(jobs, removed_ids)
            ):
                _unlink_quiet(tmp_path)
                tmp_path = None
                continue
            atomic_replace(tmp_path, jobs_file)
            tmp_path = None
            _secure_file(jobs_file)
            _preserve_file_ownership(jobs_file, _stat_before)
            # Invalidate (never refresh) the stamp: a refresh would let a nested save certify disk
            # against an OUTER caller's stale payload. Later saves take the full merge (fail-safe).
            _record_load_stamp(None)
            return
    except BaseException:
        _unlink_quiet(tmp_path)
        raise


def save_jobs(
    jobs: List[Dict[str, Any]], *, removed_ids: Optional[Collection[str]] = None,
    replace: bool = False,
):
    """Save all jobs under the lock; see ``_save_jobs_unlocked`` for ``removed_ids``/``replace``."""
    with _jobs_lock():
        _save_jobs_unlocked(jobs, removed_ids=removed_ids, replace=replace)


_MISSING = object()


def _with_job(
    job_id: Any, fn: Callable[[List[Dict[str, Any]], int, Dict[str, Any]], Any], missing: Any = None
) -> Any:
    """Run ``fn(jobs, i, job)`` on the first match under ``_jobs_lock()``; ``fn`` saves. *missing*
    if none."""
    with _jobs_lock():
        jobs = load_jobs()
        for i, job in enumerate(jobs):
            if job.get("id") == job_id:
                return fn(jobs, i, job)
    return missing


def _complete_job_record(job: Dict[str, Any]) -> None:
    """Retire *job* in place as a terminal completion (record kept for `cronjob list`)."""
    job.update(enabled=False, state="completed", next_run_at=None)


def _activate_job_record(job: Dict[str, Any]) -> None:
    """Clear pause markers in place so *job* is runnable again."""
    job.update(enabled=True, state="scheduled", paused_at=None, paused_reason=None)


# --- Run output ---

# Per-run output files (`cron/output/<job>/<timestamp>.md`) are capped so a frequent job can't fill
# the disk.
# Unlike the quick-snapshot store (`hermes_cli.backup`, capped at 20) it had no retention, so a
# frequently-scheduled job on a long-running deploy accumulated one file per run forever and could fill the
# disk (#52383). Keep the most recent N files per job; a non-positive value disables pruning (opt-out).
_CRON_OUTPUT_DEFAULT_KEEP = 50


def _cron_output_keep() -> int:
    """Per-job output-file retention cap (``cron.output_retention``)."""
    return _cron_config_number("output_retention", _CRON_OUTPUT_DEFAULT_KEEP, int)


def _prune_job_output(job_output_dir: Path, keep: int) -> int:
    """Remove the oldest ``*.md`` run-output files beyond *keep*; returns count deleted. Filenames
    are timestamps, so a reverse lexical sort is newest-first. Non-positive *keep* disables pruning;
    failures are swallowed so they can never break output saving."""
    if keep <= 0:
        return 0
    try:
        files = sorted(
            (f for f in job_output_dir.glob("*.md") if f.is_file()),
            key=lambda f: f.name, reverse=True)
    except OSError:
        return 0
    deleted = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            deleted += 1
        except OSError as exc:
            logger.debug("Failed to prune cron output %s: %s", stale.name, exc)
    return deleted


def save_job_output(job_id: str, output: str):
    """Save job output to file."""
    ensure_dirs()
    job_output_dir = _job_output_dir(job_id)
    _ensure_cron_dir(job_output_dir)
    _secure_dir(job_output_dir)
    output_file = job_output_dir / f"{_hermes_now().strftime('%Y-%m-%d_%H-%M-%S')}.md"
    atomic_write_text(output_file, output, tmp_prefix=".output_", mode=0o600)
    _secure_file(output_file)
    # Bound per-job output growth so long-running deploys don't fill the disk (#52383).
    _prune_job_output(job_output_dir, _cron_output_keep())
    return output_file


# --- Skill reference rewriting (curator integration) ---

def _canonical_skill_ref(raw: Any) -> str:
    """Reduce a job skill reference (possibly an absolute path) to the bare name the curator matches
    on, resolving as the scheduler does — otherwise a path-referencing job's skill looks
    unreferenced and gets archived. Falls back to plain cleanup so a broken import can never lose
    a name."""
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        from agent.skill_utils import normalize_skill_lookup_name
        value = normalize_skill_lookup_name(value) or value
    except Exception:
        logger.debug("referenced_skill_names: could not normalize skill ref %r", raw, exc_info=True)
    return value.strip().lstrip("/")


def referenced_skill_names() -> Set[str]:
    """Skill names referenced by ANY cron job, deliberately including paused/disabled ones (resuming
    must still find them); the curator protects these from inactivity archival. Canonicalized as the
    scheduler does, so absolute paths are protected too. A corrupt store yields an empty set."""
    try:
        jobs = load_jobs()
    except Exception:
        logger.debug("referenced_skill_names: failed to load cron jobs", exc_info=True)
        return set()
    return {
        cleaned
        for job in jobs
        if isinstance(job, dict)
        for name in _normalize_skill_list(job.get("skill"), job.get("skills"))
        if (cleaned := _canonical_skill_ref(name))
    }


def rewrite_skill_refs(
    consolidated: Optional[Dict[str, str]] = None, pruned: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Rewrite cron job skill references after a curator consolidation pass (a job listing a
    consolidated/pruned skill would otherwise run without it). Consolidated names map to their
    umbrella target without duplication, pruned names are dropped, ordering is preserved, and the
    legacy ``skill`` field is realigned. Returns ``{"rewrites": [{job_id, job_name, before,
    after, mapped, dropped}, ...], "jobs_updated": N, "jobs_scanned": M}``. Load/save exceptions
    propagate."""
    consolidated = dict(consolidated or {})
    # A skill listed in both wins as "consolidated" — it has a target, the more useful outcome.
    pruned_set = set(pruned or []) - set(consolidated.keys())
    if not consolidated and not pruned_set:
        return {"rewrites": [], "jobs_updated": 0, "jobs_scanned": 0}

    with _jobs_lock():
        jobs = load_jobs()
        rewrites: List[Dict[str, Any]] = []
        for job in jobs:
            skills_before = _normalize_skill_list(job.get("skill"), job.get("skills"))
            if not skills_before:
                continue
            mapped: Dict[str, str] = {}
            dropped: List[str] = []
            new_skills: List[str] = []
            for name in skills_before:
                if name in consolidated:
                    target = consolidated[name]
                    mapped[name] = target
                    if target and target not in new_skills:
                        new_skills.append(target)
                elif name in pruned_set:
                    dropped.append(name)
                elif name not in new_skills:
                    new_skills.append(name)
            if not mapped and not dropped:
                continue
            job["skills"] = new_skills
            job["skill"] = new_skills[0] if new_skills else None
            rewrites.append({
                "job_id": job.get("id"),
                "job_name": job.get("name") or job.get("id"),
                "before": list(skills_before),
                "after": list(new_skills),
                "mapped": mapped,
                "dropped": dropped,
            })
        if rewrites:
            save_jobs(jobs)
            logger.info("Curator rewrote skill references in %d cron job(s)", len(rewrites))
        return {"rewrites": rewrites, "jobs_updated": len(rewrites), "jobs_scanned": len(jobs)}


# Responsibilities split out of this module (re-exported for every caller).
from cron.jobs_schedule import (  # noqa: E402
    normalize_repeat_value,
    _DURATION_MULTIPLIERS,
    parse_duration,
    _WEEKDAY_TO_CRON_DOW,
    _DAYSPEC_TO_CRON_DOW,
    _parse_clock_time,
    _natural_every_to_cron,
    _cron_schedule,
    _interval_schedule,
    parse_schedule,
    _ensure_aware,
    _parse_aware,
    _timezone_offset_mismatch,
    _stored_wall_clock_is_future,
    _recoverable_oneshot_run_at,
    _MIN_GRACE_SECONDS,
    _MAX_GRACE_SECONDS,
    _compute_grace_seconds,
    _LATE_DISPATCH_TOLERANCE_SECONDS,
    _classify_dispatch_lateness,
    _cron_cadence_cache,
    _schedule_cadence_seconds,
    _cron_next_run_matches_expr,
    STALE_CRON_MATCH,
    STALE_CRON_TIMEZONE_MIGRATION,
    STALE_CRON_EXPR_EDIT,
    _classify_stale_cron_next_run,
    compute_next_run,
)
from cron.jobs_ticker import (  # noqa: E402
    _write_marker,
    record_ticker_heartbeat,
    _epoch_file_age,
    get_ticker_heartbeat_age,
    get_ticker_success_age,
    get_catch_up_occurrence_count,
    record_catch_up_occurrence,
    record_ticker_error,
    clear_ticker_error,
    get_ticker_last_error,
)
from cron.jobs_records import (  # noqa: E402
    _normalize_workdir,
    _resolve_default_model_snapshot,
    _normalize_job_optional_text,
    _normalize_base_url,
    _normalize_optional_bool,
    _normalize_str_list,
    _normalize_context_from,
    _normalize_failure_deliver,
    _normalize_local_session_origin,
    _normalize_job_approval_mode,
    _normalize_reasoning_effort,
    _CREATE_FIELD_NORMALIZERS,
    _UPDATE_FIELD_NORMALIZERS,
    _compute_provider_model_snapshots,
    _normalized_inference_axes,
    _validate_job_mode_invariants,
    _oneshot_past_grace_error,
    _next_run_or_reject_past_oneshot,
    create_job,
    get_job,
    AmbiguousJobReference,
    resolve_job_ref,
    list_jobs,
    _reject_terminal_activation,
    _normalize_job_updates,
    _rederive_repeat_for_schedule_change,
    _apply_schedule_update,
    _fill_missing_next_run,
    update_job,
    resnapshot_job,
    resnapshot_all_unpinned,
    pause_job,
    resume_job,
    trigger_job,
    _REARM_RECURRING_ERROR,
    rearm_oneshot,
    remove_job,
    _set_alert_flag,
    mark_preflight_alerted,
    clear_preflight_alerted,
    note_fire_forward_failure,
)
from cron.jobs_runs import (  # noqa: E402
    FIRE_CLAIM_TTL_SECONDS,
    FIRE_CLAIM_SKEW_SECONDS,
    _claim_owner_is_dead,
    _claim_is_live,
    _record_run_outcome,
    _advance_after_run,
    mark_job_run,
    _write_oneshot_diagnostic,
    _write_wedged_oneshot_diagnostic,
    _write_missed_oneshot_diagnostic,
    claim_dispatch,
    _refresh_claim,
    heartbeat_run_claim,
    clear_run_claim,
    advance_next_runs,
    advance_next_run,
    _machine_id,
    claim_job_for_fire,
    heartbeat_fire_claim,
)
from cron.jobs_due import (  # noqa: E402
    COMPLETED_ONESHOT_RETENTION_DAYS,
    _cron_config_number,
    _completed_oneshot_retention_days,
    _sweep_completed_oneshots,
    get_due_jobs,
    _DueScan,
    _normalize_due_scan_records,
    _self_disable_half_paused,
    _recover_missing_next_run,
    _DueJob,
    _repair_timezone_shifted_cron,
    _rearm_stale_error_recurring,
    _reanchor_stale_cron,
    _fast_forward_missed_recurring,
    _retire_expired_oneshot,
    _oneshot_dispatch_limit_reached,
    _restore_unclaimed_slot,
    _evaluate_due_job,
    _get_due_jobs_locked,
)
