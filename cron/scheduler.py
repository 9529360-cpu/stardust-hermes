"""Cron job scheduler: tick() runs due jobs (gateway calls it every 60s from a background thread).
A file lock (~/.hermes/cron/.tick.lock) keeps overlapping processes to one tick at a time.
"""

import atexit
import concurrent.futures
import contextlib
import contextvars
import errno
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# fcntl is Unix-only; Windows uses msvcrt
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol

# Must precede repo-level imports: standalone invocations (e.g. module reload after
# `hermes update`) otherwise fail with ModuleNotFoundError for hermes_time et al.
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_constants import get_hermes_home
from cron.env_settings import cron_env_setting
from hermes_cli._subprocess_compat import windows_hide_flags
from hermes_cli.config import (
    load_config, load_config_readonly, resolve_cron_model_drift_defaults)
from hermes_cli.fallback_config import get_fallback_chain
from hermes_time import now as _hermes_now
from agent.interrupt_compat import request_hard_interrupt
from agent.delegation_context import (
    enter_non_dispatcher_owned_context, exit_non_dispatcher_owned_context)
from agent.memory_provider import ctx_bound

logger = logging.getLogger(__name__)


def _close_late_session_db_result(future: "concurrent.futures.Future") -> None:
    """Done-callback: close a SessionDB whose constructor finished after run_job's init timeout
    (worker abandoned via ``shutdown(wait=False)``), else its .db/WAL/SHM handles leak to EMFILE.

    If the constructor later completes inside that abandoned worker, the Future's result — an open SessionDB
    holding .db / WAL / SHM file handles — would be orphaned and never closed, leaking descriptors until
    EMFILE (#72782). This callback retrieves and closes that eventual late result.
    """
    with contextlib.suppress(Exception):
        db = future.result()
        if db is not None:
            from hermes_state_registry import release_or_close
            release_or_close(db)


def _set_cron_session_title(session_db, session_id, base_title):
    """Persist a non-blank, unique title for a finished cron session; returns it (None if unset).
    Runs BEFORE end_session()/close() so no write races the close. Duplicate title (unique-index
    ValueError) -> get_next_title_in_lineage(); if unavailable, raise rather than end up untitled.

    Centralizes the title write so the cron finally block can guarantee a non-blank, unique title is
    persisted before end_session()/close() tear the connection down (issues #50535, #50536, #50537):
    - #50535: never leaves the session blank. base_title already carries a cron-id fallback for nameless
    jobs; this also guards a failed write. Recover by appending a #N suffix via get_next_title_in_lineage()
    when supported, instead of swallowing the error and ending up untitled. - #50536: this runs
    synchronously in the cron finally block ahead of the session close, so no in-flight title write can race
    the close.
    """
    if not session_db or not session_id:
        return None
    title = (base_title or "").strip()
    if not title:
        return None
    try:
        session_db.set_session_title(session_id, title)
        return title
    except ValueError:
        # Unique-title collision: fall back to the next lineage title (base #2, #3, ...).
        next_title_fn = getattr(session_db, "get_next_title_in_lineage", None)
        if next_title_fn is None:
            raise
        deduped = next_title_fn(title)
        if not deduped or deduped == title:
            raise
        session_db.set_session_title(session_id, deduped)
        return deduped


def _detect_gateway_code_skew() -> tuple[str, str] | None:
    """Boot-vs-disk revision skew for THIS process, or None. Test seam over
    ``gateway.code_skew.detect_code_skew``; a broken import must never take delivery down."""
    try:
        from gateway.code_skew import detect_code_skew

        return detect_code_skew()
    except Exception:
        return None


class CronTickYielded(RuntimeError):
    """A stale-code ticker yielded this tick to a fresh gateway.

    Raised by ``tick()`` BEFORE the tick lock when boot fingerprint ≠ disk, this process does NOT
    own the runtime lock and a fresh process holds it — the stale process must stay out of the
    dispatch race (contention would starve the fresh ticker). Skew ``None`` never yields (fail
    open). Raised, not returned, so ``record_ticker_error`` sees it and ``hermes cron status``
    isn't green.
    """

    def __init__(self, boot_rev: str, disk_rev: str) -> None:
        self.boot_rev = boot_rev
        self.disk_rev = disk_rev
        super().__init__(
            f"Cron tick yielded to a fresh gateway process (stale code: "
            f"booted on {boot_rev}, disk is at {disk_rev})"
        )


# Log the yield at most once per episode (reset when the skew changes) to avoid per-interval spam.
_YIELD_LOG_INTERVAL_SECONDS = 3600.0
_last_yield_log: dict[str, object] = {}


def _should_yield_tick_to_fresh_gateway() -> tuple[str, str] | None:
    """``(boot_rev, disk_rev)`` when this tick must yield to a fresher gateway, else None. Yields
    only when ALL hold: code skew, we don't own the runtime lock, another process holds it. Every
    probe failure returns None — yielding is a certainty claim, never a guess."""
    skew = _detect_gateway_code_skew()
    if skew is None:
        return None
    try:
        from gateway import status as _gateway_status
    except Exception:
        return None
    try:
        if _gateway_status.owns_gateway_runtime_lock():
            return None
        if not _gateway_status.is_gateway_runtime_lock_active():
            return None
    except Exception:
        return None
    return skew


def _log_tick_yield_once(reason: str) -> None:
    """Log the yield at error level once per episode (skew signature)."""
    global _last_yield_log
    now = time.monotonic()
    last_reason = _last_yield_log.get("reason")
    last_at = _last_yield_log.get("at", 0.0)
    if last_reason != reason or (now - float(last_at)) >= _YIELD_LOG_INTERVAL_SECONDS:
        logger.error(
            "Cron tick yielded: this process is running stale code (%s) and a "
            "fresher gateway owns the runtime lock — jobs will fire from that "
            "process. Restart this one to reclaim its ticks.",
            reason)
    _last_yield_log = {"reason": reason, "at": now}


class CronPromptInjectionBlocked(Exception):
    """Raised by _build_job_prompt when the assembled prompt (incl. runtime-loaded skill content,
    unseen by create-time scanning) trips the injection scanner; run_job turns it into a clean
    "job blocked" delivery.

    Assembled-prompt scanning (including loaded skill content) plugs the gap from #3968: create-time
    scanning only covers the user-supplied prompt field; skill content loaded at runtime was never scanned,
    so a malicious skill could carry an injection payload that reached the non-interactive (auto-approve)
    cron agent.
    """


from cron.jobs import (
    _ensure_cron_dir, advance_next_runs, claim_dispatch, claim_job_for_fire, fire_claim_fence,
    clear_run_claim, get_due_jobs, heartbeat_fire_claim, heartbeat_run_claim, mark_job_run,
    save_job_output, self_removal_delivery_allowed, self_removal_delivery_scope, use_cron_store)
from cron.executions import (
    _TERMINAL_STATES, HANDOFF_ADOPTION_GRACE_SECONDS, create_execution, finish_execution,
    get_execution, mark_execution_handoff_pending, mark_execution_running,
    recover_interrupted_executions)

# Response marker that suppresses delivery (output is still saved locally for audit).
SILENT_MARKER = "[SILENT]"
# Opt-in terminal marker for goal-oriented recurring jobs. Unlike SILENT, this is
# only interpreted when the stored job explicitly has stop_when_done=true.
DONE_MARKER = "[DONE]"


def _extract_cron_done_response(text: str) -> tuple[str, bool]:
    """Strip a standalone [DONE] first/last line and report terminal completion.

    Mid-sentence mentions are ordinary content. A marker-only response becomes a
    small user-facing completion notice so successful terminal jobs never fall
    into the empty-response soft-failure path.
    """
    lines = str(text or "").splitlines()
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return str(text or ""), False
    marker_indexes = {
        i for i in (nonempty[0], nonempty[-1])
        if lines[i].strip().upper() == DONE_MARKER
    }
    if not marker_indexes:
        return str(text or ""), False
    cleaned = "\n".join(line for i, line in enumerate(lines) if i not in marker_indexes).strip()
    # Completion must remain user-visible even if the model accidentally combines
    # the terminal marker with the ordinary silence marker.
    if cleaned and _is_cron_silence_response(cleaned):
        cleaned = ""
    return cleaned or "Task completed.", True


def _is_cron_silence_response(text: str) -> bool:
    """True when a cron final response should suppress delivery: ``[SILENT]`` (or SILENT /
    NO_REPLY / NO REPLY) as the whole response OR its own first/last line — NOT mid-sentence.
    Shares the webhook-lane matcher in :mod:`gateway.response_filters` so the two cannot drift.

    Recognizes the bracketed ``[SILENT]`` sentinel (whole-response, first line, or last line) plus the
    bracketless ``SILENT`` / ``NO_REPLY`` / ``NO REPLY`` variants the model emits when it drops the brackets
    (#51438, #46917). Whitespace-trimmed and case-insensitive. A token buried mid-sentence is treated as
    real content and delivered.
    """
    from gateway.response_filters import is_autonomous_silence_response

    return is_autonomous_silence_response(text)

# Persistent pool for parallel cron jobs: tick() submits and returns; long jobs never block it.
_parallel_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_parallel_pool_max_workers: Optional[int] = None
_running_job_ids: set = set()
_running_fire_owners: dict[str, dict[object, tuple[Optional[str], Path]]] = {}
# Parent gateway threads synchronously waiting on restart-safe scope workers.
# Shutdown must not misclassify these as ownerless in-process runs: the tool
# process sweep cannot reach the worker's transient scope.
_restart_safe_waiter_job_ids: set[str] = set()
# job_id -> pid of the restart-safe external worker executing it (absent for in-process runs), so a
# drain observer can name the process holding the gateway open.
_running_worker_pids: dict[str, int] = {}
_running_lock = threading.Lock()

# Per in-flight id: time.time() claim instant + the future owning its release (``_FUTURE_PENDING``
# until pool.submit returns). Past-allowance with no live future = leak; the sweep force-releases.
_running_since: dict = {}
_running_futures: dict = {}

# Installed in ``_running_futures`` at claim time so a sweep landing before ``pool.submit`` returns
# never sees ``missing`` and releases a claim about to get its future.
_FUTURE_PENDING = object()

# Forced-release count/history for ``get_inflight_guard_stats()``; mirrored to JSONL for probes.
_forced_release_count: int = 0
_forced_releases: list = []
_FORCED_RELEASE_HISTORY = 20

# Stale-allowance floor (minutes); per-job allowance is max(2 * interval, this).
_INFLIGHT_MIN_ALLOWANCE_MINUTES = 30.0


# Execution tokens (``_running_fire_owners`` identity keys) force-interrupted at shutdown; see
# ``mark_running_jobs_interrupted``. ``run_one_job`` checks its OWN token before writing
# ``last_status`` so a still-running agent thread can't overwrite "interrupted" with a false "ok".
# Token keying scopes the flag to one execution (recurring jobs reuse IDs); legacy paths without a
# fire owner fall back to the bare job ID.
# ``run_one_job``'s own completion path checks its OWN token before writing ``last_status`` so a cron agent
# thread that keeps running in-process after its tool was killed out from under it — and produces a
# plausible-looking final response from truncated output — can never overwrite the interrupted status with a
# false "ok" (#60432). Token keying keeps an interruption scoped to that exact execution: a later run of the
# same job ID (recurring jobs reuse the ID every fire) must not inherit the stale flag.
_interrupted_job_ids: set = set()


class _CancelEventLike(Protocol):
    """Structural type for cancellation sources (``threading.Event``, ``_CombinedCancelEvent``)."""

    def is_set(self) -> bool: ...
    def set(self) -> None: ...


class _CombinedCancelEvent:
    """Duck-typed ``threading.Event`` ORing several cancellation sources (fire-claim heartbeat
    ``lost_ownership`` + per-transport events). Workers only call is_set()/set(), so no pump thread.
    """

    def __init__(self, *events: Optional["_CancelEventLike"]) -> None:
        self._events = [event for event in events if event is not None]

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)

    def set(self) -> None:
        for event in self._events:
            event.set()


def get_running_job_ids() -> "frozenset[str]":
    """Thread-safe snapshot of executing job IDs (dispatch until ``_process_job`` returns). Read by
    the gateway shutdown drain, otherwise blind to cron work (runs outside ``_running_agents``).

    _drain_active_agents``) reads this to treat in-flight cron work as active the same way it already treats
    in-flight chat sessions via ``_running_agents`` — cron jobs run through their own thread pool here,
    entirely outside that dict, so without this the drain is structurally blind to them (#60432).
    """
    with _running_lock:
        return frozenset(_running_job_ids | _running_fire_owners.keys())


def get_running_job_details() -> list[dict]:
    """Per in-flight job: ``{"job_id", "elapsed_s", "worker_pid"}`` (``worker_pid`` None for in-process
    runs). The drain wait publishes this so ``hermes update`` can say WHICH job it is waiting on."""
    now = time.time()
    with _running_lock:
        return [
            {"job_id": jid, "elapsed_s": round(now - _running_since[jid], 1) if jid in _running_since else None,
             "worker_pid": _running_worker_pids.get(jid)}
            for jid in sorted(_running_job_ids | _running_fire_owners.keys())
        ]


def try_register_running_job(job_id: str) -> bool:
    """Atomically add ``job_id`` to the in-flight set; False (caller must skip) if already mid-run.
    Single dedupe owner for ticker + manual runs (the fire claim's 300s TTL is outlived by real
    jobs). Callers MUST pair success with ``release_running_job`` in a ``finally``.

    This is the single dedupe owner shared by the ticker's ``_submit_with_guard`` and manual runs
    (``tools/cronjob_tools``): the fire claim alone cannot prevent a double-fire because its TTL (300s) is
    routinely outlived by real jobs, after which a manual ``cronjob(action='run')`` would claim successfully
    and run the same job concurrently (idea from #53395 by @izumi0uu).
    Registration also makes the run visible to ``get_running_job_ids`` (the gateway shutdown drain, #60432)
    and ``mark_running_jobs_interrupted``.
    """
    with _running_lock:
        if job_id in _running_job_ids:
            return False
        _running_job_ids.add(job_id)
        # Same critical section as the add: no window where an in-flight id lacks an age the sweep
        # can bound. Sentinel is replaced by the real future once ``pool.submit`` returns.
        _running_since[job_id] = time.time()
        _running_futures[job_id] = _FUTURE_PENDING
        return True


def release_running_job(job_id: str) -> None:
    """Remove ``job_id`` from the in-flight running set (idempotent)."""
    with _running_lock:
        _running_job_ids.discard(job_id)
        _running_since.pop(job_id, None)
        _running_futures.pop(job_id, None)
        _running_worker_pids.pop(job_id, None)


def _inflight_min_allowance_minutes() -> float:
    """Stale allowance floor (min): ``cron.inflight_max_minutes``, else env escape hatch/default."""
    with contextlib.suppress(Exception):
        _ucfg = load_config() or {}
        _cfg_val = (
            _ucfg.get("cron", {}) if isinstance(_ucfg, dict) else {}
        ).get("inflight_max_minutes")
        if _cfg_val is not None:
            val = float(_cfg_val)
            if val > 0:
                return val
    raw = cron_env_setting("HERMES_CRON_INFLIGHT_MAX_MINUTES").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (ValueError, TypeError):
            logger.warning(
                "Invalid HERMES_CRON_INFLIGHT_MAX_MINUTES=%r; using default %s",
                raw,
                _INFLIGHT_MIN_ALLOWANCE_MINUTES)
    return _INFLIGHT_MIN_ALLOWANCE_MINUTES


# expr -> minutes; cadence never changes, so avoid re-evaluating croniter every tick.
_cron_interval_cache: dict = {}


def _cron_interval_minutes(expr: str) -> Optional[float]:
    """Cron expression cadence (gap between next two fires) in minutes; None -> floor allowance."""
    if expr in _cron_interval_cache:
        return _cron_interval_cache[expr]
    result = None
    with contextlib.suppress(Exception):
        from cron.jobs import _ensure_croniter

        if _ensure_croniter():
            from cron.jobs import croniter as _croniter
            from datetime import datetime

            base = datetime.now()
            it = _croniter(expr, base)
            first = it.get_next(datetime)
            second = it.get_next(datetime)
            gap = (second - first).total_seconds() / 60.0
            result = gap if gap > 0 else None
    _cron_interval_cache[expr] = result
    return result


def _job_interval_minutes(job: dict) -> Optional[float]:
    """Best-effort job interval in minutes (None if unknown / one-shot -> floor). ``schedule`` is
    persisted as a parsed dict; the string path is only a fallback for programmatic callers."""
    with contextlib.suppress(Exception):
        schedule = job.get("schedule")
        if isinstance(schedule, str) and schedule.strip():
            from cron.jobs import parse_schedule

            schedule = parse_schedule(schedule) or {}
        if isinstance(schedule, dict):
            kind = schedule.get("kind")
            if kind == "interval":
                minutes = schedule.get("minutes")
                return float(minutes) if minutes else None
            if kind == "cron":
                return _cron_interval_minutes(str(schedule.get("expr") or ""))
    return None


def get_inflight_guard_stats() -> dict:
    """Probe-visible snapshot; non-zero ``forced_releases`` means a job wedged and was recovered."""
    now = time.time()
    with _running_lock:
        return {
            "running": sorted(_running_job_ids),
            "running_ages_seconds": {
                jid: round(now - started, 1)
                for jid, started in _running_since.items()
            },
            "forced_releases": _forced_release_count,
            "recent_forced_releases": list(_forced_releases)}


def _record_forced_release(job_id: str, name: str, age_seconds: float, allowance_seconds: float) -> None:
    """Persist a countable signal for one forced release (best-effort)."""
    entry = {
        "job_id": job_id,
        "name": name,
        "age_seconds": round(age_seconds, 1),
        "allowance_seconds": round(allowance_seconds, 1),
        "at": _hermes_now().isoformat()}
    with _running_lock:
        _forced_releases.append(entry)
        del _forced_releases[:-_FORCED_RELEASE_HISTORY]
    try:
        path = _get_hermes_home() / "cron" / "inflight_forced_releases.jsonl"
        _ensure_cron_dir(path.parent)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as e:  # never let telemetry break a tick
        logger.debug("Could not append forced-release record: %s", e)


def _latest_executions_for_releasable_claims() -> dict:
    """Latest durable execution per releasable-looking claim (missing/pending/done future), one
    indexed query so the healthy path pays no DB work. Snapshot under _running_lock — iterating
    the set while try_register/release mutate it raises RuntimeError."""
    with _running_lock:
        claim_futures = {job_id: _running_futures.get(job_id) for job_id in _running_job_ids}
    candidates = [
        job_id for job_id, fut in claim_futures.items()
        if fut is None or fut is _FUTURE_PENDING or fut.done()
    ]
    if not candidates:
        return {}
    try:
        from cron.executions import latest_executions as _latest_execs
        return _latest_execs(candidates)
    except Exception:
        return {}


def _row_belongs_to_claim(row: dict, claim_started: float) -> bool:
    """True when the ledger row was claimed at/after this in-memory claim. An older terminal row is
    the PREVIOUS run's (try_register->create_execution window); releasing on it would
    double-dispatch. Unparseable timestamps fail closed (the age path still bounds the claim)."""
    claimed_at = row.get("claimed_at")
    if not claimed_at:
        return False
    try:
        from cron.jobs import _ensure_aware as _ensure_aware_ts
        row_ts = _ensure_aware_ts(datetime.fromisoformat(claimed_at))
        return row_ts.timestamp() >= claim_started
    except (ValueError, TypeError, OSError):
        return False


def _record_stale_release(job: dict, job_id: str, age: float, allowance: float, fut, reason: str) -> None:
    """WARNING log + probe record for one forced release, then ``last_error`` unless the ledger
    already holds the run's real outcome or the job has a finite repeat budget."""
    name = job.get("name") or job_id
    future_state = "pending" if fut is _FUTURE_PENDING else "missing" if fut is None else "finished"
    logger.warning(
        "cron.inflight.forced_release event=forced_release reason=%s job='%s' "
        "id=%s age=%.0fs allowance=%.0fs future=%s — stale in-flight claim "
        "released; the job was skipping every fire with 'already running'",
        reason, name, job_id, age, allowance, future_state)
    _record_forced_release(job_id, name, age, allowance)
    # Ledger already records how the run ended: mark_job_run here would clobber an honest
    # ok status with a synthetic failure or double-write a failure.
    if reason == "ledger-terminal":
        return
    # Age release may lack a ledger row, so last_error is how it surfaces. But a forced release
    # is NOT a real run: never consume a finite repeat budget or let mark_job_run auto-delete.
    repeat = job.get("repeat") or {}
    if isinstance(repeat, dict) and repeat.get("times") is not None:
        logger.warning(
            "cron.inflight.forced_release.job_untouched job='%s' id=%s — "
            "finite-repeat job released without mark_job_run (repeat budget "
            "preserved); row left in place so it re-fires normally",
            name, job_id)
        return
    try:
        mark_job_run(
            job_id, False,
            f"Stale in-flight claim force-released after {age / 60:.1f}m "
            f"(allowance {allowance / 60:.1f}m); previous run never released "
            f"the scheduler in-flight guard")
    except Exception as e:
        logger.warning("Could not record forced release for job %s: %s", job_id, e)


def sweep_stale_inflight(due_jobs: Optional[list] = None) -> list:
    """Force-release in-flight claims that can no longer be making progress; returns released ids.

    Stale = older than ``max(2 * interval, floor)`` AND (no live future — submit path hung before
    ``pool.submit`` returned — or finished without discarding the id) — or the claim's OWN ledger
    row is terminal regardless of age. Each release logs WARNING ``event=forced_release``, bumps
    the probe counter, mirrors JSONL, and writes ``last_error``.
    """
    global _forced_release_count

    by_id = {j.get("id"): j for j in (due_jobs or []) if isinstance(j, dict)}
    floor_seconds = _inflight_min_allowance_minutes() * 60.0
    now = time.time()
    stale: list = []
    from cron.executions import _TERMINAL_STATES as _terminal_states

    _latest = _latest_executions_for_releasable_claims()
    # Compute intervals OUTSIDE _running_lock so croniter doesn't block try_register/release.
    _intervals = {jid: _job_interval_minutes(j) for jid, j in by_id.items()}

    with _running_lock:
        for job_id in list(_running_job_ids):
            started = _running_since.get(job_id)
            if started is None:
                # Claim predates this guard — adopt it; sweepable one allowance from now.
                _running_since[job_id] = now
                continue
            age = now - started
            interval_minutes = _intervals.get(job_id)
            allowance = floor_seconds
            if interval_minutes:
                allowance = max(allowance, 2.0 * interval_minutes * 60.0)
            fut = _running_futures.get(job_id)
            if fut is _FUTURE_PENDING:
                # Submit path hung before ``pool.submit`` returned — the wedge class; release it.
                pass
            elif fut is not None and not fut.done():
                continue  # genuinely still executing
            # Ledger reconciliation: a terminal row belonging to THIS claim proves it stale even
            # inside its age allowance. Row must be this claim's, else a recurring job's previous
            # run would double-dispatch a fresh claim.
            latest = _latest.get(job_id)
            if (
                latest is not None
                and latest.get("status") in _terminal_states
                and _row_belongs_to_claim(latest, started)
            ):
                reason = "ledger-terminal"
            elif age >= allowance:
                reason = "age"
            else:
                continue
            _running_job_ids.discard(job_id)
            _running_since.pop(job_id, None)
            _running_futures.pop(job_id, None)
            _forced_release_count += 1
            stale.append((job_id, age, allowance, fut, reason))

    for job_id, age, allowance, fut, _reason in stale:
        _record_stale_release(by_id.get(job_id) or {}, job_id, age, allowance, fut, _reason)
    return [s[0] for s in stale]


def mark_running_jobs_interrupted(
    reason: str, *, only_owners: Optional[set] = None,
) -> list:
    """Best-effort: mark every in-flight cron job interrupted; returns the job IDs marked.

    Called by gateway shutdown right after ``process_registry.kill_all()``: a job whose tool was
    killed must never report success. ``only_owners`` (``(job_id, fire_owner)`` pairs) restricts
    marking. Tokens go into ``_interrupted_job_ids`` BEFORE ``last_status`` is written so
    ``run_one_job`` sees them.
    """
    with _running_lock:
        restart_safe_waiters = set(_restart_safe_waiter_job_ids)
        active_fires = [
            (token, job_id, owner, profile_home)
            for job_id, executions in _running_fire_owners.items()
            if job_id not in restart_safe_waiters
            for token, (owner, profile_home) in executions.items()
        ]
        if only_owners is not None:
            active_fires = [fire for fire in active_fires if (fire[1], fire[2]) in only_owners]
        registered_ids = {job_id for _t, job_id, _o, _p in active_fires}
        if only_owners is None:
            active_fires.extend(
                (None, job_id, None, _get_hermes_home())
                for job_id in (
                    _running_job_ids - registered_ids - restart_safe_waiters
                )
            )
        _interrupted_job_ids.update(
            token if token is not None else job_id
            for token, job_id, _owner, _profile_home in active_fires
        )
    marked = []
    for _token, job_id, fire_owner, profile_home in active_fires:
        if not fire_owner:
            logger.warning(
                "Job '%s' interrupted before its durable fire owner was registered; "
                "leaving persisted state untouched",
                job_id)
            # Still report it: shutdown uses the returned IDs for the interrupted-cron notice. The
            # in-memory flag WAS recorded above; only the persisted last_status write is skipped.
            # See #82232.
            marked.append(job_id)
            continue
        try:
            with use_cron_store(profile_home):
                if mark_job_run(
                    job_id, False, reason, expected_fire_owner=fire_owner):
                    marked.append(job_id)
        except Exception as e:
            logger.warning("Failed to mark job %s interrupted: %s", job_id, e)
    return marked


def _is_interrupted(job_id: str, token: Optional[object] = None) -> bool:
    """Non-destructive peek: has shutdown marked THIS execution interrupted? Used before deciding
    what to deliver; does not clear the flag (the authoritative pre-``last_status`` check needs it).
    ``token`` scopes to one execution so a fresh run reusing the job ID isn't poisoned."""
    with _running_lock:
        if token is not None and token in _interrupted_job_ids:
            return True
        return job_id in _interrupted_job_ids


def _consume_interrupted_flag(job_id: str, token: Optional[object] = None) -> bool:
    """Return True and clear the flag if shutdown marked THIS execution interrupted. Called right
    before ``last_status`` is written; consuming stops the flag leaking into a later run."""
    with _running_lock:
        hit = False
        if token is not None and token in _interrupted_job_ids:
            _interrupted_job_ids.discard(token)
            hit = True
        if job_id in _interrupted_job_ids:
            _interrupted_job_ids.discard(job_id)
            hit = True
        return hit


def _inactivity_watchdog_loop(
    *, get_idle_seconds: Callable[[], float], limit_s: float, poll_s: float, stop: threading.Event,
    future_done: Callable[[], bool],
) -> bool:
    """Poll idle time until limit (-> True), stop, or the future completes (-> False). Uses
    ``threading.Event.wait``, not asyncio, so a blocked event loop cannot disable the watchdog.

    Driven by ``threading.Event.wait`` (a kernel timeout), not asyncio, so a blocked event-loop /
    ``run_job`` thread cannot disable this watchdog the way ``asyncio.sleep`` / ``wait_for`` would (family A
    of #94285 — the 4118s-idle-on-a-600s-limit cron hang). Returns True when *limit_s* of inactivity was
    observed.
    """
    while not stop.wait(poll_s):
        if future_done():
            return False
        try:
            idle = float(get_idle_seconds() or 0.0)
        except Exception:
            idle = 0.0
        if idle >= limit_s:
            return True
    return False


def _cron_inactivity_seconds() -> float:
    """Parse HERMES_CRON_TIMEOUT (seconds). 0 = unlimited; bad input = 600. Shared by the
    inactivity monitor and the cwd-lock bound so they can't drift: the lock bound must stay >= the
    inactivity limit or waiters fail while a healthy holder runs."""
    raw = cron_env_setting("HERMES_CRON_TIMEOUT").strip()
    if not raw:
        return 600.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid HERMES_CRON_TIMEOUT=%r; using default 600s", raw)
        return 600.0


def _get_parallel_pool(max_workers: Optional[int]) -> concurrent.futures.ThreadPoolExecutor:
    """Return (or create) the persistent parallel pool."""
    global _parallel_pool, _parallel_pool_max_workers
    if _parallel_pool is None or _parallel_pool_max_workers != max_workers:
        if _parallel_pool is not None:
            _parallel_pool.shutdown(wait=False, cancel_futures=False)
        _parallel_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cron-parallel")
        _parallel_pool_max_workers = max_workers
    return _parallel_pool


def _shutdown_parallel_pool() -> None:
    """Shut down the persistent pool on process exit."""
    global _parallel_pool, _parallel_pool_max_workers
    if _parallel_pool is not None:
        _parallel_pool.shutdown(wait=True, cancel_futures=False)
        _parallel_pool = None
        _parallel_pool_max_workers = None


atexit.register(_shutdown_parallel_pool)
# Per-fire usage audit log; resolves via _get_hermes_home() so profile-scoped paths work.
def _usage_audit_path() -> Path:
    return _get_hermes_home() / "cron" / "usage_audit.jsonl"


def _utcnow_iso_ms() -> str:
    """RFC3339 UTC timestamp with millisecond precision and 'Z' suffix."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _write_usage_audit(record: dict) -> None:
    """Append one JSONL line to cron/usage_audit.jsonl. NEVER raises — a logger bug must not
    break cron jobs (the whole write is inside one try)."""
    try:
        path = _usage_audit_path()
        _ensure_cron_dir(path.parent)
        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("usage_audit write failed: %s", e)


def _interpreter_shutting_down(exc: Optional[BaseException] = None) -> bool:
    """True when the interpreter is finalizing (tick fired during gateway teardown): concurrent.
    futures/asyncio refuse new work, so delivery attempts only pollute errors.log — callers skip
    with a warning. ``exc`` lets an already-raised scheduling error count as a shutdown signal.

    A cron tick can fire while the gateway is tearing down — SIGTERM from ``hermes update`` / ``hermes
    gateway stop`` / systemd restart, or an OOM-kill. Once finalization starts, ``concurrent.futures``
    refuses new work with ``RuntimeError: cannot schedule new futures after interpreter shutdown`` and
    asyncio's default executor is gone, so *any* attempt to schedule delivery (live-adapter,
    ``asyncio.run``, or a fresh pool) is doomed and only pollutes ``errors.log`` with a traceback. See
    #55924, #58720.
    """
    from tools.interpreter_shutdown import interpreter_shutting_down

    return interpreter_shutting_down(exc)


# Module override hook for tests / emergency monkeypatches.
_hermes_home: Path | None = None


def _get_hermes_home() -> Path:
    """Hermes home at call time (honouring the test override). Cron is per-profile: never freeze
    this at import or anchor it at the shared default root — either breaks profile isolation.

    Cron is per-profile by design (#4707): the in-process ticker runs inside a profile-scoped gateway, so
    resolving the active HERMES_HOME at call time means a profile's jobs are stored AND executed under that
    profile's home (its .env, config.yaml, scripts, skills).
    """
    return _hermes_home or get_hermes_home()


def _get_lock_paths() -> tuple[Path, Path]:
    """Resolve cron lock paths at call time so profile/env changes are honored."""
    hermes_home = _get_hermes_home()
    lock_dir = hermes_home / "cron"
    return lock_dir, lock_dir / ".tick.lock"


# Errnos that mean "another ticker (or manual tick) holds the tick lock", as opposed to a real failure
# opening/locking the file. Everything else — most importantly EMFILE/ENFILE (fd exhaustion, #87644) and
# EACCES on open() — must be surfaced, never swallowed as lock contention.
def _is_lock_contention_errno(err: OSError) -> bool:
    """True when *err* from the lock syscall means another ticker holds the lock (POSIX flock:
    EWOULDBLOCK/EAGAIN, EACCES on some NFS; msvcrt.locking: EACCES/EDEADLK). Everything else —
    notably EMFILE/ENFILE and EACCES on open() — must be surfaced, never swallowed as contention."""
    if err.errno is None:
        return False
    if fcntl is not None:
        return err.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
    if msvcrt is not None:
        return err.errno in (errno.EACCES, errno.EDEADLK)
    return False


def _is_fd_exhaustion_text(text: str) -> bool:
    """Text half of _is_fd_exhaustion (shared with the CLI hint)."""
    lowered = text.lower()
    return "too many open files" in lowered or "emfile" in lowered


def _is_fd_exhaustion(exc: BaseException) -> bool:
    """True when *exc* indicates fd exhaustion: EMFILE/ENFILE errno, or the "Too many open files"
    wording for wrapped exceptions (load_jobs wraps the OSError in a RuntimeError).

    See #87644.
    """
    if isinstance(exc, OSError) and exc.errno in (errno.EMFILE, errno.ENFILE):
        return True
    return _is_fd_exhaustion_text(str(exc))


def _reclaim_fds_best_effort() -> None:
    """Best-effort fd reclamation: gc.collect() closes file objects stuck in reference cycles;
    apply_nofile_soft_limit() raises the RLIMIT_NOFILE soft limit for headroom. Never raises.

    The cron FD-leak family (#60859, #79742, #80792) leaks descriptors from abandoned workers/sessions. Two
    safe, idempotent levers:
    """
    with contextlib.suppress(Exception):
        import gc

        gc.collect()
    with contextlib.suppress(Exception):
        from hermes_cli.resource_limits import apply_nofile_soft_limit

        apply_nofile_soft_limit(None)


def drain_delivery_queue(adapters, loop) -> int:
    """Send queued worker results through this gateway's live adapters."""
    from cron.delivery_queue import _path, drain

    # Only restart-safe workers create the queue file.  Every gateway (macOS,
    # Windows, launchd, Docker) runs this housekeeping tick, so skip the sqlite
    # open/create entirely until a worker has actually queued something.
    if not _path().exists():
        return 0
    return drain(
        lambda queued_job, queued_content, queued_for_failure: _deliver_result(
            queued_job,
            queued_content,
            adapters=adapters,
            loop=loop,
            for_failure=queued_for_failure,
        )
    )


_DEFAULT_SCRIPT_TIMEOUT = 3600  # seconds (1 hour)
# Backward-compatible module override used by tests and emergency monkeypatches.
_SCRIPT_TIMEOUT = _DEFAULT_SCRIPT_TIMEOUT
_RUN_CLAIM_HEARTBEAT_SECONDS = 60.0
_FIRE_CLAIM_HEARTBEAT_GRACE_SECONDS = _RUN_CLAIM_HEARTBEAT_SECONDS * 3


def _cron_cleanup_timeout_seconds() -> float:
    """Return the wall-clock bound for cron post-run cleanup."""
    default = 10.0
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        cron_cfg = cfg.get("cron", {}) if isinstance(cfg, dict) else {}
        configured = cron_cfg.get("cleanup_timeout_seconds")
        if configured is not None:
            timeout = float(configured)
            if timeout >= 0:
                return timeout
    except Exception as exc:
        logger.debug("Failed to load cron cleanup timeout from config: %s", exc)
    return default


def _run_cron_cleanup_with_timeout(
    cleanup, *, job_id: str, label: str, timeout_seconds: Optional[float] = None,
) -> bool:
    """Run fallible post-run cleanup without permanently wedging a cron ID."""
    timeout = (_cron_cleanup_timeout_seconds() if timeout_seconds is None else float(timeout_seconds))
    if timeout <= 0:
        try:
            cleanup()
            return True
        except (Exception, KeyboardInterrupt) as exc:
            logger.debug("Job '%s': %s failed: %s", job_id, label, exc)
            return False

    done = threading.Event()
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            cleanup()
        except BaseException as exc:
            error.append(exc)
        finally:
            done.set()

    # Daemon thread is deliberate: unlike ThreadPoolExecutor workers it is not joined at interpreter
    # exit if cleanup never returns, so the gateway can still shut down.
    worker = threading.Thread(
        target=ctx_bound(_runner), name=f"cron-cleanup-{job_id}", daemon=True)
    worker.start()
    if not done.wait(timeout):
        logger.error(
            "Job '%s': %s exceeded %.1fs; abandoning cleanup so future runs remain dispatchable",
            job_id,
            label,
            timeout)
        return False
    if error:
        logger.debug("Job '%s': %s failed: %s", job_id, label, error[0])
        return False
    return True


class _BoundedCronSessionDB:
    """Proxy SessionDB cleanup calls through the cron cleanup timeout; after the first failure or
    timeout all later calls fail immediately (a damaged connection leaks at most one worker)."""

    def __init__(self, session_db, job_id: str):
        self._session_db = session_db
        self._job_id = job_id
        self._disabled = False

    def __getattr__(self, name):
        target = getattr(self._session_db, name)
        if not callable(target):
            return target

        def _bounded(*args, **kwargs):
            if self._disabled:
                raise RuntimeError("session finalization disabled after prior cleanup failure")

            result = {}

            def _call():
                try:
                    result["value"] = target(*args, **kwargs)
                except BaseException as exc:
                    result["error"] = exc
                    raise

            ok = _run_cron_cleanup_with_timeout(
                _call, job_id=self._job_id, label=f"session finalization ({name})")
            if not ok:
                error = result.get("error")
                if error is not None:
                    raise error
                # No error yet not complete == timeout: disable so later steps fail fast.
                self._disabled = True
                raise TimeoutError(f"session finalization method {name} timed out")
            return result.get("value")

        return _bounded


def _job_doc_header(job_name: str, job_id: str, now_iso: str, mode: str) -> str:
    """Common markdown header for the short-circuit run docs (no_agent / monitor)."""
    return (
        f"# Cron Job: {job_name}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {now_iso}\n"
        f"**Mode:** {mode}\n"
    )


def _resolve_job_workdir(job: dict, job_id: str) -> Optional[str]:
    """Configured job workdir, or None when unset / no longer a directory (logged)."""
    workdir = (job.get("workdir") or "").strip() or None
    if workdir and not Path(workdir).is_dir():
        logger.warning(
            "Job '%s': configured workdir %r no longer exists — running without it",
            job_id, workdir)
        return None
    return workdir


def _run_no_agent_job(
    job: dict, job_id: str, job_name: str, cancel_event,
) -> tuple[bool, str, str, Optional[str]]:
    """no_agent short-circuit — the script IS the job (no AIAgent, no tokens). stdout → delivered
    verbatim; empty stdout or wakeAgent=false → silent success; non-zero exit/timeout → error alert.
    """
    # Load .env first so auto-delivery can resolve *_HOME_CHANNEL: the agent path's per-run dotenv
    # reload never runs for no_agent jobs. Does not override existing values.
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=_get_hermes_home())
    except Exception:
        logger.debug("Job '%s': no_agent .env reload failed", job_id, exc_info=True)

    script_path = job.get("script")
    # Legacy/hand-edited no_agent job without a script: pause it, or it re-fires every tick.
    if not str(script_path or "").strip():
        from cron.jobs import NO_AGENT_WITHOUT_SCRIPT_ERROR

        return _block_and_pause_job(job_id, job_name, NO_AGENT_WITHOUT_SCRIPT_ERROR)

    # Pass workdir as subprocess cwd; never os.chdir() (leaks into concurrent gateway sessions).
    _job_workdir = _resolve_job_workdir(job, job_id)
    try:
        ok, output = _run_job_script_with_claim_heartbeat(
            job, script_path, workdir=_job_workdir, cancel_event=cancel_event)
    except Exception as exc:
        logger.exception("Job '%s': script execution raised unexpectedly", job_id)
        ok, output = False, f"Script execution failed: {exc}"

    now_iso = _hermes_now().strftime("%Y-%m-%d %H:%M:%S")
    header = _job_doc_header(job_name, job_id, now_iso, "no_agent (script)")

    if not ok:
        # Deliver the error: a silently broken watchdog is the worst-case outcome.
        alert = (
            f"⚠ Cron watchdog '{job_name}' script failed\n\n"
            f"{output}\n\n"
            f"Time: {now_iso}"
        )
        return False, f"{header}**Status:** script failed\n\n{output}\n", alert, output

    # wakeAgent=false is a silent signal, same as empty stdout.
    if not _parse_wake_gate(output):
        logger.info("Job '%s' (no_agent): wakeAgent=false gate — silent run", job_id)
        return True, f"{header}**Status:** silent (wakeAgent=false)\n", SILENT_MARKER, None

    if not output.strip():
        logger.info("Job '%s' (no_agent): empty stdout — silent run", job_id)
        return True, f"{header}**Status:** silent (empty output)\n", SILENT_MARKER, None

    return True, f"{header}\n---\n\n{output}\n", output, None


def _apply_monitor_gate(
    job: dict, job_id: str, job_name: str, extra_prompt: Optional[str],
) -> tuple[Optional[tuple], Optional[str]]:
    """Monitor gate (hash-suppressed change detection). Must run BEFORE any agent machinery so an
    unchanged tick costs no LLM/delivery. Returns ``(early_result | None, extra_prompt)``; when
    early_result is None, extra_prompt may carry the injected monitor context.
    """
    from cron.monitor import check_monitor, job_has_monitor

    if not job_has_monitor(job):
        return None, extra_prompt
    _mon = check_monitor(job)
    _mon_now = _hermes_now().strftime("%Y-%m-%d %H:%M:%S")
    header = _job_doc_header(job_name, job_id, _mon_now, "monitor")
    if not _mon.ok:
        # Source failure is an ERROR, never a change: alert so a broken monitor can't silently
        # stop watching. Stored hash untouched.
        logger.error("Job '%s': monitor source failed: %s", job_id, _mon.error)
        _mon_alert = (
            f"⚠ Cron monitor '{job_name}' source failed\n\n"
            f"{_mon.error}\n\n"
            f"Time: {_mon_now}"
        )
        return (
            False, f"{header}**Status:** monitor source failed\n\n{_mon.error}\n", _mon_alert, _mon.error,
        ), extra_prompt
    if not _mon.changed:
        # Unchanged: silent no_change tick (ledger doc kept; SILENT_MARKER blocks delivery).
        logger.info("Job '%s': monitor output unchanged — suppressing agent run", job_id)
        return (
            True, f"{header}**Status:** no_change (agent run suppressed)\n", SILENT_MARKER, None,
        ), extra_prompt
    # Changed (or first run): inject monitor context via the per-run seam, then normal agent run.
    if _mon.context_block:
        extra_prompt = (
            f"{_mon.context_block}\n\n{extra_prompt}" if extra_prompt else _mon.context_block
        )
    return None, extra_prompt


def _run_with_fire_claim_heartbeat(job: dict, run) -> bool:
    """Run ``run`` while keeping this job's owned durable fire claim fresh."""
    claim = job.get("fire_claim")
    owner = str(claim.get("by") or "") if isinstance(claim, dict) else ""
    if not owner:
        return run(None)

    job_id = str(job.get("id") or "")
    stop = threading.Event()
    lost_ownership = threading.Event()

    def _finish_unstarted(error: str) -> None:
        execution_id = job.get("execution_id")
        if not execution_id:
            return
        try:
            finish_execution(execution_id, success=False, error=error)
        except Exception:
            logger.warning(
                "Job '%s': failed to close unstarted execution ledger row",
                job_id,
                exc_info=True)

    try:
        owns_fire_claim = heartbeat_fire_claim(job_id, expected_owner=owner)
    except Exception:
        logger.warning("Job '%s': initial fire_claim validation failed", job_id, exc_info=True)
        _finish_unstarted("Fire claim ownership could not be validated before execution started.")
        return True

    if owns_fire_claim is False:
        logger.warning("Job '%s': fire claim ownership was already lost before execution", job_id)
        _finish_unstarted("Fire claim ownership lost before execution started.")
        return True

    def _heartbeat_loop() -> None:
        last_confirmed = time.monotonic()
        while not stop.wait(_RUN_CLAIM_HEARTBEAT_SECONDS):
            try:
                if not heartbeat_fire_claim(job_id, expected_owner=owner):
                    if self_removal_delivery_allowed(job_id):
                        # Record dropped by this run; nothing left to keep fresh.
                        continue
                    lost_ownership.set()
                    logger.warning(
                        "Job '%s': fire claim ownership lost; interrupting stale run",
                        job_id)
                    return
                last_confirmed = time.monotonic()
            except Exception:
                logger.debug("Job '%s': fire_claim heartbeat failed", job_id, exc_info=True)
                if (
                    time.monotonic() - last_confirmed
                    >= _FIRE_CLAIM_HEARTBEAT_GRACE_SECONDS
                ):
                    lost_ownership.set()
                    logger.warning(
                        "Job '%s': fire_claim could not be renewed within %.1fs; "
                        "interrupting uncertain run",
                        job_id,
                        _FIRE_CLAIM_HEARTBEAT_GRACE_SECONDS)
                    return

    heartbeat_thread = _start_heartbeat_thread(
        _heartbeat_loop, "cron-fire-claim-heartbeat",
        lambda: logger.warning(
            "Job '%s': could not start fire_claim heartbeat", job_id, exc_info=True))
    if heartbeat_thread is None:
        _finish_unstarted("Fire claim heartbeat could not be started; execution was not run.")
        return True

    try:
        return run(lost_ownership)
    finally:
        stop.set()
        heartbeat_thread.join(timeout=1.0)


def run_one_job(
    job: dict, *, adapters=None, loop=None, verbose: bool = False,
    extra_prompt: Optional[str] = None, cancel_event: Optional[_CancelEventLike] = None,
) -> bool:
    """Run ONE due job end-to-end: execute → save output → deliver → mark. Shared by the built-in
    ticker and external providers' ``fire_due``; does NOT decide due-ness or acquire the initial
    claim (callers use the store CAS) but keeps it alive. True if processed (a job failure is
    recorded via ``mark_job_run``), False only if processing raised. ``cancel_event``: optional
    transport-level cancel (dashboard drain)."""
    # Every gateway path (built-in scheduler, external providers, and direct
    # API fires) crosses this seam.  Ensure the detached worker has a durable
    # attempt to adopt before any launch can occur.
    if not job.get("execution_id"):
        execution = create_execution(
            job["id"], source="direct", scheduled_instant=job.get("_scheduled_instant"))
        job["execution_id"] = execution["id"]

    execution_id = str(job["execution_id"])
    external_owner = os.environ.get("_HERMES_CRON_EXTERNAL_WORKER") == execution_id
    if not external_owner:
        try:
            if _launch_external_cron_worker(job):
                return True
        except Exception as handoff_error:
            error = f"Restart-safe cron worker dispatch failed: {handoff_error}"
            logger.error("Job '%s': %s", job["id"], error)
            claim = job.get("fire_claim")
            owner = str(claim.get("by") or "") if isinstance(claim, dict) else ""
            try:
                mark_job_run(
                    job["id"],
                    False,
                    error,
                    **({"expected_fire_owner": owner} if owner else {}),
                )
            finally:
                finish_execution(execution_id, success=False, error=error)
            return True
    if extra_prompt is None:
        # Gateway-forwarded manual run stamps its prompt on the job via trigger_job; the fire that
        # consumes the manual occurrence picks it up here. Single-fire: mark_job_run clears it.
        _stamped = job.get("manual_run_prompt")
        if _stamped and job.get("manual_run_at"):
            extra_prompt = str(_stamped)
    claim = job.get("fire_claim")
    fire_owner = str(claim.get("by") or "") if isinstance(claim, dict) else ""
    execution_token = object()
    profile_home = _get_hermes_home().resolve()
    with _running_lock:
        _running_fire_owners.setdefault(job["id"], {})[execution_token] = (
            fire_owner or None, profile_home)
    try:
        with self_removal_delivery_scope(job["id"]):
            return _run_with_fire_claim_heartbeat(
                job,
                lambda lost_ownership: _run_one_job_body(
                    job,
                    adapters=adapters,
                    loop=loop,
                    verbose=verbose,
                    extra_prompt=extra_prompt,
                    fire_claim_lost=(
                        _CombinedCancelEvent(lost_ownership, cancel_event)
                        if cancel_event is not None
                        else lost_ownership
                    ),
                    execution_token=execution_token))
    finally:
        with _running_lock:
            executions = _running_fire_owners.get(job["id"])
            if executions is not None:
                executions.pop(execution_token, None)
                if not executions:
                    _running_fire_owners.pop(job["id"], None)


def _run_one_job_body(
    job: dict, *, adapters=None, loop=None, verbose: bool = False,
    extra_prompt: Optional[str] = None, fire_claim_lost: Optional[_CancelEventLike] = None,
    execution_token: Optional[object] = None,
) -> bool:
    fence = _FireOwnership(job, fire_claim_lost)
    fire_owner = fence.owner
    _side_effect_fence = fence.side_effect_fence
    _fire_claim_ownership_lost = fence.lost

    execution_id = job.get("execution_id")
    if not execution_id:
        execution_id = create_execution(
            job["id"], source="direct", scheduled_instant=job.get("_scheduled_instant"))["id"]
    delivery_attempted = False
    delivery_error = None
    from agent.secret_scope import (
        build_profile_secret_scope, reset_secret_scope, set_secret_scope)

    _scope_token = None
    _terminal_scope_token = None
    try:
        # Commit a finite one-shot's dispatch BEFORE its side effect so a tick dying mid-run cannot
        # re-fire it forever on restart. No-op for recurring/infinite jobs (at-most-times).
        # This lives here in the shared body so BOTH the built-in ticker and the external provider (Chronos
        # fire_due) get at-most-times semantics. See #38758.
        if not claim_dispatch(job["id"]):
            logger.info(
                "Job '%s': one-shot dispatch limit reached — skipping",
                job.get("name", job["id"]))
            finish_execution(
                execution_id, success=False,
                error="Dispatch claim rejected; execution was not started.")
            return True  # not an error — already handled/removed

        # Claimed durably before dispatch; becomes running only right before the actual run.
        # Detached workers transition to running while adopting; in-process paths must win the
        # claimed->running CAS here before any user script or agent side effect may begin.
        external_owner = os.environ.get("_HERMES_CRON_EXTERNAL_WORKER") == execution_id
        if not external_owner and mark_execution_running(execution_id) is None:
            logger.warning("Cron job %s lost execution ownership before start; skipping", job["id"])
            return True

        # get_secret() fails closed outside a scope; the ticker thread has none. Delivery adapters
        # resolve credentials, so the scope must span delivery too (reset in the outer finally).
        _scope_token = set_secret_scope(build_profile_secret_scope(_get_hermes_home()))
        # Same for terminal policy (gateway/run.py _profile_runtime_scope): else the ticker reads
        # process-global TERMINAL_* env a concurrent profile pinned. Resolution failure installs a
        # refusal scope — terminal execution raises instead of using the launch process's policy.
        # Same isolation for terminal settings (third profile seam; see gateway/run.py
        # _profile_runtime_scope): installs the firing profile's COMPLETE terminal policy for this fire —
        # run, delivery, and bookkeeping — resetting in this function's finally alongside the secret scope.
        # See #68559.
        # Bind the profile's COMPLETE terminal policy for the agent build (fail-closed: malformed policy →
        # refusal scope) so _make_agent's terminal probing / cwd hints resolve the routed profile, never the
        # launch process (#98581 class).
        # Same authoritative terminal policy the gateway binds per turn (#68559): a docker-configured
        # dashboard profile must never resolve the launch process's pinned env.
        # Fourth profile seam: bind the session profile's COMPLETE terminal policy for this turn
        # (dashboard/TUI analogue of the gateway's per-turn scope). #98581's unified-desktop reproduction
        # ran a docker-configured profile on the host because terminal_tool read the launch process's pinned
        # env.
        from tools.terminal_scope import (
            install_profile_terminal_scope)

        _terminal_scope_token = install_profile_terminal_scope(_get_hermes_home())
        # Defer agent teardown until AFTER delivery; closing first races the live send against a
        # torn-down async client. run_job hands the agent back via this list.
        # Defer the cron agent's async-resource teardown until AFTER delivery. run_job normally closes the
        # agent (and reaps stale async clients) in its finally block; doing that before _deliver_result runs
        # means the live send races a torn-down async client (#58720). Passing a holder list makes run_job
        # hand the agent back instead, and we tear it down below once delivery is done. Defense-in-depth
        # alongside the interpreter-shutdown guard in _deliver_result.
        _deferred_agents: list = []

        def _teardown_deferred() -> None:
            # run_job's finally still hands back the agent when it raises; tear it down here so a failed run
            # never leaks its async resources (#10200), then re-raise into the outer handler. BaseException
            # (not just Exception) so a KeyboardInterrupt/SystemExit mid-run still triggers teardown before
            # propagating.
            # Tear down the deferred agent(s) now that save + delivery have run (or raised). Must happen on
            # every path so cron agents never leak their subprocesses/clients (#10200).
            for _deferred_agent in _deferred_agents:
                _teardown_cron_agent(_deferred_agent, job["id"])

        _run_kwargs = {
            "defer_agent_teardown": _deferred_agents,
            "extra_prompt": extra_prompt,
            "execution_id": execution_id}
        if fire_claim_lost is not None:
            _run_kwargs["cancel_event"] = fire_claim_lost
        try:
            success, output, final_response, error = run_job(job, **_run_kwargs)
        except BaseException:
            # run_job hands back the agent even when raising; tear down so a failed run never leaks.
            # BaseException so KeyboardInterrupt/SystemExit mid-run still trigger teardown.
            _teardown_deferred()
            raise

        if _fire_claim_ownership_lost():
            _teardown_deferred()
            _record_fire_ownership_lost(job["id"], fire_owner, execution_id)
            return True

        terminal_complete = False
        if success and job.get("stop_when_done"):
            final_response, terminal_complete = _extract_cron_done_response(final_response)

        # Agent is still live through delivery; wrap ALL of save/compose/deliver in try/finally so a
        # raise anywhere still tears the deferred agent down.
        d = _RunDelivery(
            job=job, success=success, error=error, terminal_complete=terminal_complete)
        try:
            _save_compose_deliver(
                d, fence, final_response, output, adapters=adapters, loop=loop, verbose=verbose,
                execution_token=execution_token)
            _publish_local_session_completion(d, fence, execution_id)
        except _FireClaimLostDuringSideEffect:
            d.side_effect_ownership_lost = True
        finally:
            delivery_attempted, delivery_error = d.delivery_attempted, d.delivery_error
            # Every path must tear down deferred agent(s) so they never leak subprocesses/clients.
            _teardown_deferred()

        if d.side_effect_ownership_lost or _fire_claim_ownership_lost():
            _record_fire_ownership_lost(job["id"], fire_owner, execution_id)
            return True

        # Empty final_response is a soft failure so last_status is not "ok".
        if d.success and not final_response.strip():
            d.success = False
            d.error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

        if _consume_interrupted_flag(job["id"], execution_token):
            _finish_interrupted_run(job, execution_id, delivery_error)
            return True

        return _finish_completed_run(d, fire_owner, execution_id)

    except BaseException as e:  # noqa: BLE001 — deliberate: see below
        # BaseException, not Exception: CancelledError/KeyboardInterrupt/SystemExit propagate here.
        # Without mark_job_run(False) a finite one-shot is wedged: claim_dispatch consumed
        # repeat.completed but last_run_at is never written. Record first, then re-raise
        # non-Exception. Owner fencing still applies.
        # BaseException, not Exception (#73973): the inner run_job handler re-raises CancelledError /
        # KeyboardInterrupt / SystemExit after agent teardown, and none of those are Exception subclasses.
        # If they escape without mark_job_run(False), a finite one-shot is left wedged — claim_dispatch()
        # already consumed repeat.completed, but last_run_at is never written, so the job sits in state
        # "scheduled" until the run-claim TTL expires and the dispatch-limit guard removes it with no output
        # and no error. Owner fencing still applies: a stale worker must not record over a replacement claim
        # owner.
        _err_text = str(e) or type(e).__name__
        logger.error(
            "Error processing job %s: %s",
            job["id"],
            _err_text,
            exc_info=(type(e), e, e.__traceback__))
        delivery_outcome = "suppressed"
        # Owner fencing: a stale worker whose claim was taken over (or transport-cancelled) must not
        # send a failure alert on top of the replacement run's; fall through to fenced bookkeeping.
        if (
            isinstance(e, Exception)
            and not delivery_attempted
            and not isinstance(e, _FireClaimLostDuringSideEffect)
            and not _fire_claim_ownership_lost()
        ):
            delivery_error, delivery_outcome = _deliver_crash_failure(
                job, _err_text, adapters=adapters, loop=loop)
        try:
            if (
                not _consume_interrupted_flag(job["id"], execution_token)
                and not self_removal_delivery_allowed(job["id"])  # no record left to mark
            ):
                mark_kwargs = {}
                if fire_owner is not None:
                    mark_kwargs["expected_fire_owner"] = fire_owner
                if isinstance(e, Exception):
                    mark_kwargs["delivery_error"] = delivery_error
                mark_job_run(job["id"], False, _err_text, **mark_kwargs)
        except Exception as record_err:
            # Never let bookkeeping mask the original interruption.
            logger.error("Failed to record interrupted run for job %s: %s", job["id"], record_err)
        try:
            finish_execution(
                execution_id, success=False, error=_err_text, delivery_outcome=delivery_outcome)
        except Exception as record_err:
            logger.error("Failed to finish execution record for job %s: %s", job["id"], record_err)
        if not isinstance(e, Exception):
            raise
        return False
    finally:
        # Function-level on purpose: must scope delivery, deferred teardown, claim-loss handling and
        # bookkeeping — not just run_job. Do not move into the run block's finally.
        if _scope_token is not None:
            reset_secret_scope(_scope_token)
        if _terminal_scope_token is not None:
            from tools.terminal_scope import reset_terminal_scope

            reset_terminal_scope(_terminal_scope_token)


def _notify_provider_jobs_changed() -> None:
    """Best-effort: tell the active scheduler provider the job set changed. Call AFTER a successful
    store mutation so an external provider can re-provision/cancel the one-shot; no-op for the
    built-in. Kept out of cron/jobs.py (import cycle). Never raises."""
    try:
        from cron.scheduler_provider import resolve_cron_scheduler
        resolve_cron_scheduler().on_jobs_changed()
    except Exception as e:
        logger.debug("on_jobs_changed notify failed: %s", e)


class CronSchedulerRegistrationError(RuntimeError):
    """A job was persisted but its first external trigger was not registered."""

    def __init__(self, job: dict, cause: Exception) -> None:
        self.job = job
        self.cause = cause
        super().__init__(
            f"Cron job '{job['id']}' was saved, but its first scheduler "
            f"registration failed ({type(cause).__name__}). Do not create a "
            "duplicate. Pause/resume or update the job to retry registration."
        )

    def user_message(self) -> str:
        """Human-facing variant for chat/CLI surfaces (no exception class name)."""
        label = self.job.get("name") or self.job["id"]
        return (
            f"Saved cron job '{label}', but couldn't register it with the "
            "external scheduler yet. The job is kept — don't re-create it; "
            "pause/resume or edit it (e.g. via /cron) to retry registration."
        )

    def to_dict(self) -> dict:
        """Return the public partial-failure contract without provider details."""
        return {
            "error": str(self),
            "job_id": self.job["id"],
            "job_saved": True,
            "scheduler_registered": False,
            "retry_create": False}


def register_persisted_job(job: dict) -> dict:
    """Register an already-durable job with the active provider.

    External providers key registration by the persisted job identity. This is also the
    reconciliation path for a caller recovering from an unknown create outcome.
    """
    if not job.get("enabled", True):
        return job
    from cron.scheduler_provider import resolve_cron_scheduler

    try:
        resolve_cron_scheduler().register_job(job)
    except Exception as exc:
        raise CronSchedulerRegistrationError(job, exc) from exc
    return job


def create_job_with_scheduler_registration(**kwargs) -> dict:
    """Persist one job and register its first trigger with the active provider."""
    from cron.jobs import create_job

    return register_persisted_job(create_job(**kwargs))


# Dead-owner reap is throttled (opens the executions ledger). Tests may reset
# _last_dead_owner_reap_at to None to force a reap next tick.
# Dead-owner claim reclaim throttle (#86721): recover_interrupted_executions opens the executions ledger, so
# the per-tick reap is rate-limited rather than run on every idle 60s cycle.
_DEAD_OWNER_REAP_INTERVAL_SECONDS = 300.0
_last_dead_owner_reap_at: Optional[float] = None

# Worktree prune throttle: the cron tick is the only reliably periodic process on gateway boxes.
_WORKTREE_MAINTENANCE_INTERVAL_SECONDS = 6 * 3600.0
_last_worktree_maintenance_at: Optional[float] = None
_worktree_maintenance_lock = threading.Lock()


def _worktree_maintenance_repos() -> List[str]:
    """Repos whose ``.worktrees/`` to keep pruned: the hermes checkout plus job workdir repo roots,
    filtered to those that actually have a ``.worktrees/`` dir."""
    repos: set = set()

    # Hermes source checkout (git installs only; wheel installs have no .git).
    with contextlib.suppress(Exception):
        install_root = Path(__file__).resolve().parent.parent
        if (install_root / ".git").exists():
            repos.add(str(install_root))

    with contextlib.suppress(Exception):
        from cron.jobs import load_jobs

        for job in load_jobs():
            workdir = str(job.get("workdir") or "").strip()
            if not workdir or not Path(workdir).is_dir():
                continue
            try:
                probe = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=5, cwd=workdir)
                if probe.returncode == 0 and probe.stdout.strip():
                    repos.add(probe.stdout.strip())
            except Exception:
                continue

    return [r for r in sorted(repos) if (Path(r) / ".worktrees").is_dir()]


def _maybe_run_worktree_maintenance() -> None:
    """Throttled worktree prune from the cron tick, on a daemon thread so the tick never waits on
    git. Same conservative pruner as ``hermes -w`` startup (dirty/unpushed/locked trees untouched).
    Errors never propagate: GC is hygiene, not scheduling."""
    global _last_worktree_maintenance_at
    now = time.monotonic()
    with _worktree_maintenance_lock:
        if (
            _last_worktree_maintenance_at is not None
            and now - _last_worktree_maintenance_at
            < _WORKTREE_MAINTENANCE_INTERVAL_SECONDS
        ):
            return
        _last_worktree_maintenance_at = now

    def _run() -> None:
        try:
            repos = _worktree_maintenance_repos()
            if not repos:
                return
            from cli import _prune_stale_worktrees

            for repo in repos:
                try:
                    _prune_stale_worktrees(repo)
                except Exception:
                    logger.debug("Cron worktree maintenance failed for %s", repo, exc_info=True)
        except Exception:
            logger.debug("Cron worktree maintenance skipped", exc_info=True)

    threading.Thread(target=_run, name="cron-worktree-prune", daemon=True).start()


def _acquire_tick_lock(lock_file):
    """Open + non-blocking lock the tick file (fcntl / msvcrt). Returns the fd, or None on genuine
    contention. A real OSError (esp. EMFILE/ENFILE) must NOT pass as contention — the scheduler
    would look healthy while no job runs — so it is re-raised for the ticker to record a FAILED
    tick."""
    lock_fd = None
    try:
        # Cross-platform file locking: fcntl on Unix, msvcrt on Windows. Only genuine lock contention
        # (another ticker holds the lock) skips the tick silently. A real OSError — most importantly
        # EMFILE/ENFILE from fd exhaustion — must NOT be swallowed as "another instance holds the lock":
        # that previously made the scheduler appear healthy (tick returned 0, heartbeat recorded success)
        # while no job ever ran again (#87644).
        lock_fd = open(lock_file, "w", encoding="utf-8")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_fd
    except OSError as exc:
        if lock_fd is not None:
            with contextlib.suppress(OSError):
                lock_fd.close()
            if _is_lock_contention_errno(exc):
                logger.debug("Tick skipped — another instance holds the lock")
                return None
        if _is_fd_exhaustion(exc):
            # fd reclamation is the ticker loop's job (scheduler_provider.py); here would double it.
            logger.error(
                "Cron tick could not acquire tick lock: %s — scheduler will "
                "attempt fd reclamation and retry with backoff",
                exc)
        else:
            logger.error("Cron tick could not acquire tick lock: %s", exc)
        raise


def _release_tick_lock(lock_fd) -> None:
    if fcntl:
        with contextlib.suppress((OSError, IOError)):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    elif msvcrt:
        with contextlib.suppress((OSError, IOError)):
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
    lock_fd.close()


def _maybe_reap_dead_owners() -> None:
    """Dead-owner reclaim: a run that died mid-flight would leave its row 'claimed' forever. Only
    rows whose owner process is proved gone are touched (_owner_is_live). Throttled."""
    # Dead-owner claim reclaim (#86721): execution rows carry their owner pid + process start time, but
    # recovery previously ran only at scheduler STARTUP. A one-shot `hermes cron run` that claimed a job and
    # died mid-run (its runner thread lived in the exiting CLI process) left the row 'claimed' forever while
    # the long-lived gateway ticker kept running — blocking every future run of that job. Reap provably-dead
    # owners periodically so stale claims auto-clear without a gateway restart. Throttled so idle 60s ticks
    # don't pay a ledger connection every cycle (#33612).
    global _last_dead_owner_reap_at
    _reap_now = time.monotonic()
    if (
        _last_dead_owner_reap_at is not None
        and _reap_now - _last_dead_owner_reap_at < _DEAD_OWNER_REAP_INTERVAL_SECONDS
    ):
        return
    _last_dead_owner_reap_at = _reap_now
    try:
        from cron.executions import recover_interrupted_executions

        _reclaimed = recover_interrupted_executions()
        if _reclaimed:
            logger.warning(
                "Reclaimed %d cron execution(s) whose owner process died "
                "before reaching a terminal state (marked unknown)",
                _reclaimed)
    except Exception as _reap_exc:
        logger.debug("Dead-owner execution reclaim failed: %s", _reap_exc)


def _sweep_stale_inflight_for_tick(due_jobs: list) -> None:
    """Bound the in-flight set BEFORE the dedup guard so a leaked claim is force-released now
    rather than eating every later fire until restart. Skipped when nothing is in flight."""
    if not _running_job_ids:
        return
    _sweep_jobs = due_jobs
    with contextlib.suppress(Exception):
        _inflight_ids = set(_running_job_ids)
        _due_ids = {j.get("id") for j in due_jobs if isinstance(j, dict)}
        if not _inflight_ids <= _due_ids:
            from cron.jobs import load_jobs as _load_all_jobs

            _sweep_jobs = _load_all_jobs()
    try:
        sweep_stale_inflight(_sweep_jobs)
    except Exception as e:
        logger.warning("Stale in-flight sweep failed: %s", e)


def _resolve_max_parallel_workers() -> Optional[int]:
    """Max workers: env > config.yaml > unbounded (HERMES_CRON_MAX_PARALLEL=1 restores serial)."""
    try:
        _env_par = cron_env_setting("HERMES_CRON_MAX_PARALLEL").strip()
        if _env_par:
            return int(_env_par) or None
    except (ValueError, TypeError):
        logger.warning("Invalid HERMES_CRON_MAX_PARALLEL value; defaulting to unbounded")
    with contextlib.suppress(Exception):
        _ucfg = load_config() or {}
        _cfg_par = (_ucfg.get("cron", {}) if isinstance(_ucfg, dict) else {}).get("max_parallel_jobs")
        if _cfg_par is not None:
            return int(_cfg_par) or None
    return None


def _sweep_mcp_orphans() -> None:
    """Reap MCP stdio orphans (only PIDs flagged by tools.mcp_tool._run_stdio's finally block);
    run AFTER jobs finish so live sessions are never touched."""
    try:
        from tools.mcp_tool_lifecycle import _kill_orphaned_mcp_children
        _kill_orphaned_mcp_children()
    except Exception as _e:
        logger.debug("Post-tick MCP orphan cleanup failed: %s", _e)


def _process_due_job(job: dict, adapters, loop, verbose: bool) -> bool:
    """Run one due job via the shared ``run_one_job`` body."""
    # Claim only when the worker actually starts, so a queued lease can't expire first.
    claimed = claim_job_for_fire(job["id"], return_job=True)
    if not claimed:
        finish_execution(
            job["execution_id"], success=False, error="Fire claim lost; execution was not started.")
        return True
    # CAS returns the persisted record; bool fallback only for older test doubles.
    claimed_job = dict(claimed) if isinstance(claimed, dict) else dict(job)
    claimed_job["execution_id"] = job["execution_id"]
    claimed_job["_scheduled_instant"] = job.get("_scheduled_instant")
    return run_one_job(claimed_job, adapters=adapters, loop=loop, verbose=verbose)


def _submit_with_guard(job: dict, pool: concurrent.futures.ThreadPoolExecutor, process_job):
    """Submit with the in-flight dedup guard; None if a prior tick's run is still in flight.
    Running-set membership is released in the worker's finally."""
    job_id = job["id"]
    job_label = job.get("name", job_id)

    def _clear_run_claim_best_effort() -> None:
        """Best-effort claim cleanup on dispatch-failure paths. Only one-shots carry a run_claim;
        clear_run_claim takes _jobs_lock + full load/save and can raise on degraded paths
        (shutdown, EMFILE) — a claim expiring at TTL beats crashing the tick.

        Only one-shot jobs carry a ``run_claim`` (stamped by get_due_jobs, #59229), so recurring jobs skip
        the call entirely — clear_run_claim acquires _jobs_lock (blocking cross-process flock) and does a
        full load_jobs read, and the dispatch-failure paths fire exactly when the process can least afford N
        pointless lock/read round-trips (interpreter shutdown, EMFILE).  clear_run_claim itself does
        load_jobs/save_jobs file I/O; on those degraded paths it can raise, and these early-exits exist
        precisely to skip cleanly — a stale claim expiring at the TTL is a better outcome than crashing the
        tick (#86522).
        """
        _schedule = job.get("schedule")
        if not (isinstance(_schedule, dict) and _schedule.get("kind") == "once"):
            return
        try:
            clear_run_claim(job_id)
        except Exception as claim_err:
            logger.warning(
                "Could not clear run_claim for job '%s' after dispatch "
                "failure: %s (claim will expire at TTL)",
                job_label, claim_err)

    def _not_dispatched_shutdown() -> None:
        logger.warning("Job '%s' not dispatched — interpreter is shutting down", job_label)

    # During interpreter shutdown pool.submit raises; skip — the job fires on the next tick.
    # If the interpreter is finalizing (gateway SIGTERM / restart / OOM), scheduling any new delivery is
    # futile — asyncio.run and a fresh ThreadPoolExecutor both raise "cannot schedule new futures after
    # interpreter shutdown". Skip gracefully with a warning rather than emitting an ERROR traceback on every
    # restart-race (#58720, #55924).
    # A tick can race gateway teardown: once the interpreter is finalizing, ``pool.submit`` raises "cannot
    # schedule new futures after interpreter shutdown" and crashes the tick. Skip cleanly — the job stays
    # due and will fire on the next healthy tick (#58720, #55924).
    if _interpreter_shutting_down():
        _not_dispatched_shutdown()
        _clear_run_claim_best_effort()
        return None
    if not try_register_running_job(job_id):
        logger.info("Job '%s' already running — skipping", job_label)
        return None
    # Record the attempt before dispatch; recovery marks abandoned rows unknown (no retry).
    try:
        execution = create_execution(
            job_id, source="builtin", scheduled_instant=job.get("_scheduled_instant"))
        dispatched_job = dict(job, execution_id=execution["id"])
        _ctx = contextvars.copy_context()
    except Exception as execution_err:
        # Release the claim so the next tick retries instead of wedging "already running".
        release_running_job(job_id)
        _clear_run_claim_best_effort()
        logger.exception(
            "Job '%s' not dispatched: execution creation failed: %s", job_label, execution_err)
        return None

    def _run_and_release(j=dispatched_job, ctx=_ctx):
        try:
            return ctx.run(process_job, j)
        finally:
            release_running_job(j["id"])

    try:
        fut = pool.submit(_run_and_release)
    except Exception as submit_err:
        release_running_job(job_id)
        _clear_run_claim_best_effort()
        finish_execution(
            execution["id"], success=False, error=f"Executor dispatch failed: {submit_err}")
        if isinstance(submit_err, RuntimeError) and _interpreter_shutting_down(submit_err):
            _not_dispatched_shutdown()
        else:
            logger.error("Job '%s' not dispatched: %s", job_label, submit_err)
        return None

    with _running_lock:
        if job_id in _running_job_ids:
            _running_futures[job_id] = fut
    return fut


def _sweep_mcp_orphans_when_all_done(futures: list) -> None:
    """Async (gateway ticker) mode: sweep via a done-callback after the LAST job completes; sweep
    inline when nothing was dispatched (all skipped / no due jobs)."""
    if not futures:
        _sweep_mcp_orphans()
        return
    _remaining = [len(futures)]

    def _on_done(_f: concurrent.futures.Future) -> None:
        _remaining[0] -= 1
        with contextlib.suppress(Exception):
            _exc = _f.exception()
            if _exc is not None:
                logger.error(
                    "Cron job future failed in async mode: %s", _exc,
                    exc_info=(type(_exc), _exc, _exc.__traceback__))
        if _remaining[0] <= 0:
            _sweep_mcp_orphans()

    for _f in futures:
        _f.add_done_callback(_on_done)


def tick(
    verbose: bool = True, adapters=None, loop=None, sync: bool = True, *, can_dispatch=None):
    """Check and run all due jobs. File-locked so only one tick runs at a time (gateway ticker vs
    standalone daemon / manual tick). ``can_dispatch``: optional gate; false leaves due jobs for the
    next allowed tick. Returns the number of jobs executed (0 if another tick holds the lock)."""
    # Stale-code yield gate — BEFORE the lock race. A process whose checkout was updated under it
    # serves mixed sys.modules (jobs die on ImportErrors); if a fresher gateway holds the runtime
    # lock, ITS ticker dispatches. With no fresh holder (desktop-standalone) the tick proceeds.
    _skew = _should_yield_tick_to_fresh_gateway()
    if _skew is not None:
        _log_tick_yield_once(f"boot={_skew[0]} disk={_skew[1]}")
        raise CronTickYielded(_skew[0], _skew[1])

    lock_dir, lock_file = _get_lock_paths()
    _ensure_cron_dir(lock_dir)
    lock_fd = _acquire_tick_lock(lock_file)
    if lock_fd is None:
        return 0

    try:
        # `hermes pause` ESTOP: skip dispatch, never touch in-flight runs; check_paused logs once.
        with contextlib.suppress(ImportError):
            from agent.estop import check_paused as _estop_check_paused
            if _estop_check_paused("cron", logger):
                return 0

        if can_dispatch is not None and not can_dispatch():
            logger.debug("Cron dispatch paused while gateway drains existing work")
            return 0

        from cron.bot_chat_delivery import drain, drain_in_background
        if sync:
            drain()
        else:
            drain_in_background()
        _maybe_reap_dead_owners()
        # Periodic worktree GC (6h, threaded) — the only sweep gateway-only boxes get.
        try:
            _maybe_run_worktree_maintenance()
        except Exception as _wt_exc:
            logger.debug("Worktree maintenance dispatch failed: %s", _wt_exc)

        due_jobs = get_due_jobs()
        _sweep_stale_inflight_for_tick(due_jobs)

        if not due_jobs:
            # Idle tick: skip config load + pool setup, but still reap crashed jobs' MCP orphans.
            if verbose:
                # Idle tick: skip config load + pool partitioning entirely (#33612 — the gateway ticker
                # calls tick(verbose=False) every 60s, so idle ticks previously fell through to
                # load_config()). Still run the post-tick MCP orphan sweep: main intentionally sweeps on
                # idle ticks so orphaned stdio children from crashed jobs are reaped even when nothing is
                # due.
                logger.info("%s - No jobs due", _hermes_now().strftime('%H:%M:%S'))
            _sweep_mcp_orphans()
            return 0

        if verbose:
            logger.info("%s - %s job(s) due", _hermes_now().strftime('%H:%M:%S'), len(due_jobs))

        # Advance next_run_at for recurring jobs FIRST, under the lock, before any execution
        # (at-most-once). Re-advancing running jobs keeps the grace window alive; mark_job_run
        # overwrites it on completion. Composes with the claim-time advance in claim_job_for_fire.
        advance_next_runs([job["id"] for job in due_jobs])

        _max_workers = _resolve_max_parallel_workers()
        if verbose:
            logger.info(
                "Running %d job(s) in parallel (max_workers=%s)",
                len(due_jobs),
                _max_workers if _max_workers else "unbounded")

        def _process_job(job: dict) -> bool:
            return _process_due_job(job, adapters, loop, verbose)

        # Persistent pool, non-blocking dispatch. Already-running jobs are skipped; mark_job_run
        # re-arms next_run_at on completion, so no catch-up queue is needed.
        _results: list = []
        _all_futures: list = []
        pool = _get_parallel_pool(_max_workers)
        for job in due_jobs:
            fut = _submit_with_guard(job, pool, _process_job)
            if fut is None:
                continue
            _all_futures.append(fut)
            if not sync:
                _results.append(True)  # optimistically counted

        if sync:
            for f in concurrent.futures.as_completed(_all_futures):
                try:
                    _results.append(f.result())
                except Exception as exc:
                    logger.error("Cron job future failed: %s", exc)
                    _results.append(False)
            _sweep_mcp_orphans()
            return sum(_results)

        _sweep_mcp_orphans_when_all_done(_all_futures)
        return sum(_results)
    finally:
        _release_tick_lock(lock_fd)


# ---------------------------------------------------------------------------
# Split modules. Imported at the bottom (import cycle: they late-bind ``cron.scheduler`` as
# ``_sched``). Only names this module itself calls; everything else lives in the split module.
# ---------------------------------------------------------------------------
from cron.scheduler_delivery import (  # noqa: E402
    _deliver_result, _delivery_lane_value, _normalize_deliver_value, _resolve_delivery_target,
    _resolve_delivery_targets,
)
from cron.scheduler_script import (  # noqa: E402
    _get_session_db_timeout, _run_job_script_with_claim_heartbeat, _start_heartbeat_thread,
)
from cron.scheduler_prompt import (  # noqa: E402
    _block_and_pause_job, _build_job_prompt, _guard_job_credential_exfil, _parse_wake_gate,
)
from cron.scheduler_preflight import (  # noqa: E402
    BLOCKED_CONFIG_MARKER, BLOCKED_CONFIG_SILENT_MARKER, _cron_preflight_enabled,
    _empty_requested_mcp_toolsets, _is_transient_provider_resolve_error, _preflight_job_config,
)


# Stateless responsibilities moved out of this module (re-exported for every caller).
from cron.scheduler_failures import (  # noqa: E402
    _fallback_chain_phrase,
    _failure_streak_nudge,
    _summarize_cron_failure_for_delivery,
    _upsert_incident_for_failure,
    _resolve_incidents_for_recovered_job,
    _mark_incident_alerted,
)
from cron.scheduler_job_runtime import (  # noqa: E402
    _resolve_cron_disabled_toolsets,
    _merge_mcp_into_per_job_toolsets,
    _resolve_cron_enabled_toolsets,
    _resolve_job_reasoning_config,
    _CronJobConfig,
    _snapshot_pin,
    _load_cron_job_config,
    _load_prefill_messages,
    _preflight_or_block,
    _blocked_config_result,
    _resolve_job_runtime,
    _load_credential_pool,
    _init_cron_mcp_tools,
    _open_cron_session_db,
    _CronAgentSetup,
    _resolve_cron_agent_setup,
    _construct_cron_agent,
)
from cron.scheduler_agent_run import (  # noqa: E402
    _raise_inactivity_timeout,
    _run_agent_with_watchdog,
    _final_response_from_result,
    _finalize_cron_session,
    _run_doc_header,
    _RunResult,
    _prepare_job_prompt,
    _CRON_DELIVERY_VARS,
    _CronRunScope,
    _reload_dotenv_and_publish_delivery_target,
    _FireAudit,
    run_job,
    _teardown_cron_agent,
)
from cron.scheduler_run_outcome import (  # noqa: E402
    _OWNERSHIP_LOST_INTERRUPTED,
    _record_fire_ownership_lost,
    _classify_delivery_outcome,
    _compose_run_delivery,
    _FireClaimLostDuringSideEffect,
    _FireOwnership,
    _RunDelivery,
    _save_compose_deliver,
    _publish_local_session_completion,
    _finish_interrupted_run,
    _finish_completed_run,
    _deliver_crash_failure,
)
from cron.scheduler_external_worker import (  # noqa: E402
    _wait_for_external_cron_worker_body,
    _wait_for_external_cron_worker,
    _launch_external_cron_worker,
    _run_external_worker_payload,
)


# `python -m cron.scheduler` entry: MUST stay below the split-module imports so the worker /
# tick paths see every name they need.
if __name__ == "__main__":
    if "--external-worker-file" in sys.argv:
        import argparse

        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--external-worker-file", type=Path, required=True)
        parser.add_argument("--ack-file", type=Path, required=True)
        args = parser.parse_args()
        # The gateway spawns this worker with stdout/stderr on DEVNULL; without
        # a handler every adoption/ack failure below would be invisible.
        try:
            from hermes_logging import setup_logging

            setup_logging(hermes_home=_get_hermes_home(), mode="cron")
        except Exception:
            pass
        raise SystemExit(
            0 if _run_external_worker_payload(args.external_worker_file, args.ack_file) else 1
        )
    tick(verbose=True)
