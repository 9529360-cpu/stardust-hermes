"""Store operations for event triggers: startup arming, folder polls, the per-job queue, the hourly
run budget, the retry-once rule and skipped records.

Split out of ``cron.jobs``, which re-exports every name here. Names this module does not define are
reached late-bound via ``_jobs``. The pure model (validation, deny list, debounce) lives in
``cron.triggers``. Anything that changes persisted state runs under ``_jobs._jobs_lock()``. Folder
listing I/O runs outside that lock, so a slow network share cannot hold up other cron writers.
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from cron import triggers as _triggers
from cron.triggers import (
    QUEUE_CAP,
    RUNS_PER_HOUR,
    RUN_WINDOW_SECONDS,
    SKIPPED_HISTORY,
    file_metadata,
    initial_trigger_state,
    is_event_triggered,
    observe_folder,
    poll_due,
    trigger_type,
    watch_dir_denial,
)

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module

_STATE = "trigger_state"

# Process-level startup bookkeeping. ``_ticker_started`` is set when the built-in ticker starts,
# which the gateway does only after its adapters are up. ``_armed_stores`` holds the cron stores
# whose startup triggers already fired in this process, so a respawned ticker thread cannot run
# them a second time within the same start.
_ticker_started = False
_armed_stores: set = set()


def mark_ticker_started() -> None:
    """Called when the built-in ticker starts. Startup triggers arm on that process's next tick."""
    global _ticker_started
    _ticker_started = True


def _state_of(job: Dict[str, Any], now: float) -> Dict[str, Any]:
    state = job.get(_STATE)
    if not isinstance(state, dict):
        state = job[_STATE] = initial_trigger_state(job.get("trigger") or {}, now)
    return state


def _event_job_active(job: Dict[str, Any]) -> bool:
    return is_event_triggered(job) and _jobs.is_job_runnable(job) and not _jobs.is_terminal_job(job)


def _new_event(kind: str, now: float, file: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {"id": uuid.uuid4().hex[:12], "kind": kind, "attempt": 1, "queued_at": now}
    if file is not None:
        event["file"] = file
    return event


def _record_skip(state: Dict[str, Any], event: Dict[str, Any], reason: str, now: float) -> None:
    skipped = state.setdefault("skipped", [])
    skipped.append({
        "at": now,
        "kind": event.get("kind"),
        "file": (event.get("file") or {}).get("name"),
        "reason": reason,
    })
    del skipped[:-SKIPPED_HISTORY]
    state["skipped_total"] = int(state.get("skipped_total") or 0) + 1
    logger.info("Event trigger skipped (%s): %s", reason, event.get("kind"))


def _enqueue(state: Dict[str, Any], event: Dict[str, Any], now: float) -> bool:
    queue = state.setdefault("queue", [])
    if len(queue) >= QUEUE_CAP:
        _record_skip(state, event, "queue_full", now)
        return False
    queue.append(event)
    return True


def _runs_in_window(state: Dict[str, Any], now: float) -> List[float]:
    """Run start times inside the rolling hour. Also prunes the stored list."""
    window = [stamp for stamp in state.get("run_times") or [] if now - float(stamp) < RUN_WINDOW_SECONDS]
    state["run_times"] = window
    return window


def _arm_startup(jobs: List[Dict[str, Any]], now: float) -> bool:
    """Once per store per ticker start. Clears hand-outs a previous process never reported, drops
    startup events a previous start left queued, and queues one startup run per active startup job.
    Returns True when any event job was touched, so a store with no event jobs is never rewritten."""
    touched = False
    for job in jobs:
        if not is_event_triggered(job):
            continue
        touched = True
        state = _state_of(job, now)
        state.pop("inflight", None)
        if trigger_type(job) != "startup":
            continue
        state["queue"] = [event for event in state.get("queue") or [] if event.get("kind") != "startup"]
        if _event_job_active(job):
            _enqueue(state, _new_event("startup", now), now)
    return touched


def _apply_listing(state: Dict[str, Any], entries: List[Dict[str, Any]], now: float) -> None:
    state.pop("last_error", None)
    for entry in observe_folder(state, entries, now):
        _enqueue(state, _new_event("folder", now, file_metadata(entry)), now)


def _note_poll_error(job_id: Any, state: Dict[str, Any], message: str) -> None:
    if state.get("last_error") != message:
        logger.warning("Folder trigger for job %s: %s", job_id, message)
    state["last_error"] = message


def _folder_plans(now: float) -> Tuple[List[Tuple[str, str, str]], bool]:
    """Phase A: which active folder jobs are due for a poll, plus whether any event job exists."""
    with _jobs._jobs_lock():
        plans: List[Tuple[str, str, str]] = []
        has_event_jobs = False
        for job in _jobs.load_jobs():
            if not is_event_triggered(job):
                continue
            has_event_jobs = True
            if trigger_type(job) != "folder" or not _event_job_active(job):
                continue
            spec = job["trigger"]
            state = job.get(_STATE) if isinstance(job.get(_STATE), dict) else {}
            if poll_due(state, int(spec["poll_seconds"]), now):
                plans.append((str(job["id"]), spec["watch_dir"], spec["pattern"]))
        return plans, has_event_jobs


def _list_folders(
    plans: Iterable[Tuple[str, str, str]], lister: Callable[[str, str], List[Dict[str, Any]]],
) -> Tuple[Dict[str, Tuple[str, str, List[Dict[str, Any]]]], Dict[str, str]]:
    """Phase B, outside the jobs lock: list each due folder. The deny list is re-checked here, so a
    watch folder that now resolves to a system path is refused even if it passed at creation."""
    listings: Dict[str, Tuple[str, str, List[Dict[str, Any]]]] = {}
    errors: Dict[str, str] = {}
    for job_id, watch_dir, pattern in plans:
        denial = watch_dir_denial(watch_dir)
        if denial:
            errors[job_id] = f"watch folder is not allowed: {denial}"
            continue
        try:
            listings[job_id] = (watch_dir, pattern, lister(watch_dir, pattern))
        except OSError as exc:
            errors[job_id] = f"watch folder could not be read: {exc}"
    return listings, errors


def _take_dispatches(
    jobs: List[Dict[str, Any]], running_ids: set, now: float,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Hand out at most one queued event per idle job. Events that exceed the hourly budget are
    recorded as skipped and dropped. Returns job copies that carry ``trigger_event``."""
    handed: List[Dict[str, Any]] = []
    changed = False
    for job in jobs:
        if not _event_job_active(job) or str(job.get("id")) in running_ids:
            continue
        state = _state_of(job, now)
        queue = state.setdefault("queue", [])
        while queue:
            event = queue.pop(0)
            changed = True
            window = _runs_in_window(state, now)
            if len(window) >= RUNS_PER_HOUR:
                _record_skip(state, event, "rate_limit", now)
                continue
            window.append(now)
            state["last_triggered_at"] = now
            state["inflight"] = dict(event, dispatched_at=now)
            handed_job = copy.deepcopy(job)
            handed_job["trigger_event"] = dict(event, dispatched_at=now)
            handed_job["_execution_source"] = event["kind"]
            handed.append(handed_job)
            break
    return handed, changed


def collect_trigger_dispatches(
    running_ids: Iterable[str], *, now: Optional[float] = None,
    list_folder: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """One trigger pass inside a scheduler tick (the caller holds the tick lock).

    Arms startup triggers once per store per ticker start, polls the due folder watches, then hands
    out at most one queued event per idle job. The returned copies are submitted like due jobs."""
    t = _triggers.now_epoch() if now is None else float(now)
    lister = list_folder or _triggers.list_watch_folder
    store_key = str(_jobs._current_cron_store().cron_dir)
    arm = _ticker_started and store_key not in _armed_stores
    plans, has_event_jobs = _folder_plans(t)
    if not (arm or has_event_jobs):
        return []
    listings, errors = _list_folders(plans, lister)
    with _jobs._jobs_lock():
        jobs = _jobs.load_jobs()
        changed = _arm_startup(jobs, t) if arm else False
        for job in jobs:
            job_id = str(job.get("id"))
            if trigger_type(job) != "folder" or not _event_job_active(job):
                continue
            state = _state_of(job, t)
            if job_id in errors:
                _note_poll_error(job_id, state, errors[job_id])
            elif job_id in listings:
                watch_dir, pattern, entries = listings[job_id]
                if (job["trigger"]["watch_dir"], job["trigger"]["pattern"]) != (watch_dir, pattern):
                    continue  # the folder changed while it was being listed; the next poll covers it
                _apply_listing(state, entries, t)
            else:
                continue
            state["last_poll_at"] = t
            changed = True
        handed, took = _take_dispatches(jobs, set(running_ids), t)
        if changed or took:
            _jobs.save_jobs(jobs)
    if arm:
        _armed_stores.add(store_key)
    return handed


def return_trigger_dispatch(job_id: Any, event: Dict[str, Any]) -> bool:
    """Undo one hand-out whose run never started (submit refused, or the fire claim is held
    elsewhere). The event returns to the head of the queue and its run slot is released."""
    stamp = event.get("dispatched_at")
    restored = {key: value for key, value in event.items() if key != "dispatched_at"}

    def apply(jobs, _i, job):
        state = _state_of(job, _triggers.now_epoch())
        inflight = state.get("inflight")
        if isinstance(inflight, dict) and inflight.get("id") == restored.get("id"):
            state.pop("inflight", None)
        if stamp is not None:
            run_times = list(state.get("run_times") or [])
            if stamp in run_times:
                run_times.remove(stamp)
            state["run_times"] = run_times
        state.setdefault("queue", []).insert(0, restored)
        _jobs.save_jobs(jobs)
        return True

    return bool(_jobs._with_job(job_id, apply, missing=False))


def record_trigger_outcome(job: Dict[str, Any], success: bool) -> None:
    """Called by ``mark_job_run`` under the jobs lock once a run finishes. A failed first attempt is
    queued once more at the head of the queue. A second failure is not retried: the error already
    lives in ``last_status`` and ``last_error``."""
    if not is_event_triggered(job):
        return
    state = job.get(_STATE)
    if not isinstance(state, dict):
        return
    inflight = state.pop("inflight", None)
    if success or not isinstance(inflight, dict) or int(inflight.get("attempt") or 1) >= 2:
        return
    if not _event_job_active(job):
        return
    retry = {key: value for key, value in inflight.items() if key != "dispatched_at"}
    retry["attempt"] = 2
    state.setdefault("queue", []).insert(0, retry)
    logger.info("Job '%s': event run failed; retrying once", job.get("name", job.get("id")))


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
