"""The due scan: completed one-shot retention, record repair, missing/timezone/stale-schedule
repairs, catch-up, one-shot gates and ``get_due_jobs``.

Split out of ``cron.jobs``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_jobs`` (import-cycle breaking), so
monkeypatching ``cron.jobs.<name>`` keeps working.
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module


# Completed one-shots are retained in jobs.json (final status stays inspectable) and pruned by
# _sweep_completed_oneshots once they age out.
COMPLETED_ONESHOT_RETENTION_DAYS = 7


def _cron_config_number(key: str, default: Any, cast: Callable[[Any], Any]) -> Any:
    """Read ``cron.<key>`` from config as *cast*, falling back to *default* on any failure."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        cron_cfg = cfg.get("cron", {}) if isinstance(cfg, dict) else {}
        return cast(cron_cfg.get(key, default))
    except Exception:
        return cast(default)


def _completed_oneshot_retention_days() -> float:
    """``cron.completed_retention_days``; non-positive disables the sweep (records kept forever)."""
    return _cron_config_number("completed_retention_days", COMPLETED_ONESHOT_RETENTION_DAYS, float)


def _sweep_completed_oneshots(
    raw_jobs: List[Dict[str, Any]], now: datetime, *, removed_ids: Optional[Set[str]] = None,
) -> bool:
    """Prune completed one-shot records past retention (in place; True when anything was removed).
    Removed ids go into *removed_ids* so save_jobs's shrink-merge guard allows the delete. Age is
    measured from ``last_run_at``; a record without a parseable one is kept (never guess into
    deletion)."""
    retention_days = _jobs._completed_oneshot_retention_days()
    if retention_days <= 0:
        return False
    cutoff = now - timedelta(days=retention_days)
    removed = False
    for rj in list(raw_jobs):
        try:
            if rj.get("state") != "completed":
                continue
            schedule = rj.get("schedule")
            if (schedule.get("kind") if isinstance(schedule, dict) else None) != "once":
                continue
            last_run = rj.get("last_run_at")
            last_run_dt = _jobs._parse_aware(last_run) if isinstance(last_run, str) else None
            if last_run_dt is None or last_run_dt >= cutoff:
                continue
            raw_jobs.remove(rj)
            removed = True
            rid = rj.get("id")
            if removed_ids is not None and rid:
                removed_ids.add(str(rid))
            logger.info(
                "Job '%s': pruning completed one-shot record (finished %s, retention %.1f days)",
                rj.get("name", rj.get("id", "?")), last_run, retention_days)
        except Exception:
            logger.debug(
                "Retention sweep skipped malformed job record %r", rj.get("id", "?"), exc_info=True)
    return removed


# --- Due scan ---

def get_due_jobs() -> List[Dict[str, Any]]:
    """Return all jobs due now. A recurring job more than one period stale (gateway down, or a run
    overran the interval) has its backlog collapsed — next_run_at fast-forwards so nothing
    burst-fires — but still fires ONCE now (via mark_job_run, consuming one ``repeat.times`` run),
    avoiding the perpetual-defer loop for runs longer than interval + grace.

    This prevents the perpetual-defer loop (#33315) where a job whose runtime exceeds ``interval + grace``
    would be skipped forever.
    """
    with _jobs._jobs_lock():
        return _get_due_jobs_locked()


@dataclass
class _DueScan:
    """Mutable state threaded through one due scan: the raw store records plus what to persist."""

    raw_jobs: List[Dict[str, Any]]
    now: datetime
    needs_save: bool = False
    removed: Set[str] = field(default_factory=set)

    def find(self, job_id: Any) -> Optional[Dict[str, Any]]:
        return next((rj for rj in self.raw_jobs if rj["id"] == job_id), None)

    def persist(self, job_id: Any, **fields: Any) -> None:
        """Write *fields* onto the raw record for *job_id* and flag a save (no-op if missing)."""
        rj = self.find(job_id)
        if rj is not None:
            rj.update(fields)
            self.needs_save = True

    def retire(self, job_id: Any) -> None:
        """Drop the raw record for *job_id* as an intentional removal."""
        rj = self.find(job_id)
        if rj is not None:
            self.raw_jobs.remove(rj)
            self.removed.add(str(job_id))
            self.needs_save = True


def _normalize_due_scan_records(raw_jobs: List[Dict[str, Any]]) -> bool:
    """Repair malformed store records in place BEFORE the due scan keys off them: a missing ``id``
    (older writers used ``job_id``), non-dict ``schedule``, or non-ISO timestamp used to abort the
    whole scan before save_jobs(), freezing the scheduler in a fast-forward loop."""
    changed = False
    for rj in raw_jobs:
        if not rj.get("id"):
            rj["id"] = rj.pop("job_id", None) or uuid.uuid4().hex[:12]
            changed = True
        if not isinstance(rj.get("schedule"), dict):
            rj["schedule"] = {}
            changed = True
        for key in ("next_run_at", "last_run_at"):
            value = rj.get(key)
            if value is not None and _jobs._parse_aware(value) is None:
                rj.pop(key, None)  # the "no next_run_at" path recomputes
                changed = True
    return changed


def _self_disable_half_paused(job: Dict[str, Any], scan: _DueScan) -> None:
    """Self-heal enabled=true with pause markers: the operator believes the job is frozen while the
    scheduler would still fire it. Force enabled=false so listings are honest; logged loudly since
    pause_job sets both fields atomically, so this should be rare."""
    jid = job.get("id")
    logger.error(
        "Job '%s' (%s) has pause markers while enabled=true; "
        "self-disabling so it cannot fire (pause must be authoritative).",
        job.get("name", jid), jid)
    rj = scan.find(jid)
    if rj is None:
        return
    rj.update(enabled=False, state="paused")
    if not rj.get("paused_at"):
        rj["paused_at"] = scan.now.isoformat()
    if not rj.get("paused_reason"):
        rj["paused_reason"] = "auto-disabled: enabled+paused contradiction"
    scan.needs_save = True


def _recover_missing_next_run(job: Dict[str, Any], scan: _DueScan) -> Optional[str]:
    """Recompute and persist a missing ``next_run_at``; None when unrecoverable. One-shots use the
    grace window; recurring jobs only get here after a direct jobs.json edit bypassed add_job(),
    and would otherwise be silently skipped forever."""
    schedule = job.get("schedule", {})
    kind = schedule.get("kind")
    recovered_next = _jobs._recoverable_oneshot_run_at(
        schedule, scan.now, last_run_at=job.get("last_run_at"))
    recovery_kind = "one-shot" if recovered_next else None
    if not recovered_next and kind in {"cron", "interval"}:
        recovered_next = _jobs.compute_next_run(schedule, scan.now.isoformat())
        if recovered_next:
            recovery_kind = kind
    if not recovered_next:
        return None
    job["next_run_at"] = recovered_next
    logger.info(
        "Job '%s' had no next_run_at; recovering %s run at %s",
        job.get("name", job.get("id", "?")), recovery_kind, recovered_next)
    scan.persist(job["id"], next_run_at=recovered_next)
    return recovered_next


@dataclass
class _DueJob:
    """One candidate under evaluation: its record, schedule and the stored next_run in raw/aware
    form."""

    job: Dict[str, Any]
    scan: _DueScan
    next_run: str  # stored ISO string, compared string-exact against manual_run_at
    raw_next_run_dt: datetime  # as stored (may carry a pre-migration offset)
    next_run_dt: datetime  # normalized to the configured tz

    @property
    def schedule(self) -> Dict[str, Any]:
        return self.job.get("schedule", {})

    @property
    def kind(self) -> Optional[str]:
        return self.schedule.get("kind")

    @property
    def label(self) -> Any:
        return self.job.get("name", self.job.get("id", "?"))

    def recompute_next(self) -> Optional[str]:
        return _jobs.compute_next_run(self.schedule, self.scan.now.isoformat())


def _repair_timezone_shifted_cron(d: _DueJob) -> bool:
    """Repair a cron job whose stored offset no longer matches now's (TZ migration).

    next_run_at is an absolute instant but the expr means local wall clock, so a TZ change can make
    it look due hours early. If the stored wall clock is still in the future, recompute so we fire
    at the intended local time. True when re-anchored (caller skips this tick). TRADE-OFF: a DST
    offset change meeting the same conditions SKIPS the pending occurrence; accepted as rare."""
    now = d.scan.now
    if not (
        d.next_run_dt <= now
        and _jobs._timezone_offset_mismatch(d.raw_next_run_dt, now)
        and _jobs._stored_wall_clock_is_future(d.raw_next_run_dt, now)
    ):
        return False
    new_next = d.recompute_next()
    if not new_next:
        return False
    logger.info(
        "Job '%s' next_run_at offset changed (%s -> %s). "
        "Recomputing cron run to preserve local wall-clock intent: %s",
        d.label, d.raw_next_run_dt.utcoffset(), now.utcoffset(), new_next)
    d.scan.persist(d.job["id"], next_run_at=new_next)
    return True


def _rearm_stale_error_recurring(d: _DueJob) -> datetime:
    """Re-arm a recurring job wedged in persisted last_status=error; returns the effective
    next_run_dt.

    Such a job errored, mark_job_run parked next_run_at in the future, and nothing re-dispatched it
    (the in-memory stale-claim sweep cannot see it). Interval jobs re-arm to now (always a legal
    fire); cron jobs re-arm to the next LEGAL occurrence, since re-arming to now would fire at times
    the expression excludes. A correctly-parked cron value is left as-is.
    """
    now = d.scan.now
    if not (
        d.kind in ("cron", "interval")
        and d.next_run_dt > now
        and _jobs._job_is_stale_error_recurring(d.job, d.schedule, now)
    ):
        return d.next_run_dt
    if d.kind == "interval":
        recovered_next = now.isoformat()
        recovered_next_dt: Optional[datetime] = now
    else:
        recovered_next = d.recompute_next()
        recovered_next_dt = _jobs._parse_aware(recovered_next) if recovered_next else None
    if not (recovered_next and recovered_next_dt is not None and recovered_next_dt < d.next_run_dt):
        return d.next_run_dt
    jid = d.job.get("id")
    logger.warning(
        "cron.persisted_error.recovered job='%s' id=%s — recurring "
        "job wedged in stale last_status=error without re-firing for "
        "a full cadence; re-arming next_run_at to %s so it re-dispatches without force-run/resume",
        d.job.get("name", jid), jid, recovered_next)
    _jobs._record_persisted_error_recovery(d.job, d.next_run)
    d.job["next_run_at"] = recovered_next
    d.scan.persist(jid, next_run_at=recovered_next)
    return recovered_next_dt


def _reanchor_stale_cron(d: _DueJob) -> bool:
    """Stale-schedule guard for a due cron instant; True when re-anchored without firing.

    A direct edit of schedule.expr leaves next_run_at on the old lattice, so re-anchor first (from
    the current expr, so this converges). An offset-representation migration also moves a legacy
    instant off the lattice, and re-anchoring THAT swallowed a due occurrence — so classify, and
    let
    the migration case fall through to fire ONCE (at-most-once holds: nothing re-reads the legacy
    instant after advance/mark_job_run rewrites it)."""
    stale_class = _jobs._classify_stale_cron_next_run(d.schedule, d.raw_next_run_dt, d.next_run_dt)
    if stale_class == _jobs.STALE_CRON_EXPR_EDIT:
        new_next = d.recompute_next()
        logger.info(
            "Job '%s' next_run_at %s does not match its current "
            "cron expression %r (direct jobs.json edit?); re-anchoring to %s without firing.",
            d.label, d.next_run, d.schedule.get("expr"), new_next)
        if new_next:
            d.scan.persist(d.job["id"], next_run_at=new_next)
        return True
    if stale_class == _jobs.STALE_CRON_TIMEZONE_MIGRATION:
        logger.warning(
            "cron.timezone_migration.catch_up job='%s' id=%s expr=%r "
            "stored=%s normalized=%s — stored next_run_at carries a "
            "pre-migration UTC offset (%s, now %s) and is a legal "
            "occurrence at its own wall clock; firing the due run instead of re-anchoring past it.",
            d.label, d.job.get("id"), d.schedule.get("expr"), d.next_run,
            d.next_run_dt.isoformat(), d.raw_next_run_dt.utcoffset(), d.scan.now.utcoffset())
        _jobs._record_timezone_migration_catchup(d.job, d.raw_next_run_dt, d.next_run_dt)
    return False


def _fast_forward_missed_recurring(d: _DueJob, grace: int) -> bool:
    """Re-anchor accumulated misses; return whether catch-up was explicitly disabled.

    The fast-forward is persisted immediately — NOT redundant with advance_next_run/mark_job_run:
    it
    protects the crash window before mark_job_run and covers the external fire_due path, which never
    calls advance_next_run. mark_job_run re-anchors on completion, so the value is provisional.
    """
    if (d.scan.now - d.next_run_dt).total_seconds() <= grace:
        return False
    new_next = d.recompute_next()
    if not new_next:
        return False
    d.scan.persist(d.job["id"], next_run_at=new_next)
    if (_jobs._ensure_aware(datetime.fromisoformat(new_next)) > d.scan.now
            and not _cron_config_number("catch_up_missed", True, lambda value: value is not False)):
        logger.info(
            "Job '%s' missed its scheduled time (%s, grace=%ds). "
            "Skipping missed occurrence because cron.catch_up_missed is false; next run: %s",
            d.label, d.next_run, grace, new_next)
        return True
    logger.info(
        "Job '%s' missed its scheduled time (%s, grace=%ds). "
        "Running now; next run provisionally set to: %s (re-anchored on completion)",
        d.label, d.next_run, grace, new_next)
    _jobs.record_catch_up_occurrence()
    return False


def _retire_expired_oneshot(d: _DueJob) -> bool:
    """One-shot grace gate; True when the job must not fire this tick.

    A one-shot beyond the grace window must never fire (create/update/resume reject such schedules
    and recovery never revives them; only the due scan used to dispatch them hours late). With no
    claim stamped, retire it with a diagnostic (never silently delete). A claim may mean a run is
    still in flight elsewhere — skip but keep the record so its mark_job_run can land."""
    if (d.scan.now - d.next_run_dt).total_seconds() <= _jobs.ONESHOT_GRACE_SECONDS:
        return False
    if not (d.job.get("run_claim") or d.job.get("fire_claim")):
        _jobs._write_missed_oneshot_diagnostic(d.job, d.next_run)
        d.scan.retire(d.job["id"])
    return True


def _oneshot_dispatch_limit_reached(job: Dict[str, Any], scan: _DueScan) -> bool:
    """One-shot dispatch-limit guard; True when the job must not fire this tick.

    A finite one-shot claimed via claim_dispatch() whose tick died before mark_job_run has
    completed >= times while still looking due. Remove it instead of re-firing — unless THIS
    process is still running it (a run outliving the run_claim TTL is slow, not stale)."""
    repeat = job.get("repeat") or {}
    times = repeat.get("times")
    completed = repeat.get("completed", 0)
    if times is None or times <= 0 or completed < times:
        return False
    name = job.get("name", job.get("id", "?"))
    # A live run must never have its job record deleted underneath it (#62002): a run that outlives the
    # run_claim TTL (stream stall, laptop asleep mid-run) satisfies the same completed >= times +
    # expired-claim condition as a dead tick, but mark_job_run() still needs the record to land last_run_at
    # / last_status / last_delivery_error. If this process is still running the job, it is slow, not stale —
    # keep the entry and skip.
    if _jobs._job_running_in_this_process(job.get("id", "")):
        logger.info(
            "Job '%s': dispatch limit reached (%d/%d) but its run is still in flight in this "
            "process — keeping entry",
            name, completed, times)
        return True
    if job.get("last_run_at") is not None:
        # A record with last_run_at completed a real run and was re-armed without a budget reset
        # (old build or hand edit) — not the dead-tick case; warn so the removal leaves a trace.
        logger.warning(
            "Job '%s': one-shot dispatch limit reached (%d/%d) on a record that already completed "
            "a run (last_run_at=%s) — removing it WITHOUT firing. This record was re-armed "
            "without a budget reset (pre-#93615 store or hand edit); re-run it with "
            "'hermes cron resume <job> --run-now' (#93524).",
            name, completed, times, job.get("last_run_at"))
    else:
        logger.info(
            "Job '%s': one-shot dispatch limit reached (%d/%d) — removing stale due entry",
            name, completed, times)
    scan.retire(job["id"])
    # The claimed run never completed here by definition — leave an operator-visible diagnostic.
    _jobs._write_wedged_oneshot_diagnostic(job)
    return True


def _restore_unclaimed_slot(job: Dict[str, Any], scan: _DueScan) -> Optional[str]:
    """Put an occurrence the dispatcher advanced past but never claimed back on the schedule
    (#107485); returns the restored ``next_run_at`` or None. Restored ONCE: the stamp is dropped
    here, so the slot then meets the ordinary late / fast-forward / ``cron.catch_up_missed``
    policy like any other overdue instant — never a replay of every missed slot."""
    from cron.occurrences import unclaimed_pending_slot

    slot = unclaimed_pending_slot(job, scan.now)
    if slot is None:
        return None
    logger.warning(
        "Job '%s' (%s): occurrence %s was taken off the schedule but never claimed "
        "(scheduler stopped before dispatch); restoring it as the due instant (was %s).",
        job.get("name", job.get("id")), job.get("id"), slot, job.get("next_run_at"))
    job["next_run_at"] = slot
    job.pop("pending_slot", None)
    rj = scan.find(job["id"])
    if rj is not None:
        rj.pop("pending_slot", None)
    scan.persist(job["id"], next_run_at=slot)
    return slot


def _evaluate_due_job(job: Dict[str, Any], scan: _DueScan, run_claim_ttl: float) -> bool:
    """Decide whether one enabled, non-terminal job fires this tick, persisting any repairs.
    Ordering matters: recover missing next_run_at, repair timezone shifts, re-arm stale-error
    recurring jobs; then once due: re-anchor stale cron instants, fast-forward missed recurring
    runs, retire/guard one-shots, and finally stamp the run claim / dispatch record."""
    now = scan.now
    # Cross-process guard: another process's live one-shot run_claim (younger than TTL) — do NOT
    # re-dispatch. Malformed/future-dated claims (clock/TZ skew) count as stale, never eternally
    # fresh.
    if (
        job.get("schedule", {}).get("kind") == "once"
        and _jobs._claim_is_live(job.get("run_claim"), now, run_claim_ttl)
    ):
        return False

    next_run = _restore_unclaimed_slot(job, scan) or job.get("next_run_at") or _recover_missing_next_run(job, scan)
    if not next_run:
        return False
    raw_next_run_dt = datetime.fromisoformat(next_run)
    d = _DueJob(job, scan, next_run, raw_next_run_dt, _jobs._ensure_aware(raw_next_run_dt))
    kind = d.kind
    recurring = kind in {"cron", "interval"}
    # Intentionally string-exact on raw stored values: trigger_job stamps the SAME isoformat string
    # into both fields, and any rewrite of next_run_at (edit, re-anchor, fire-claim advance) must
    # invalidate the marker. Do not "fix" this with _ensure_aware normalization.
    manual_run = job.get("manual_run_at") == next_run
    from cron.occurrences import completed_occurrence, scheduled_instant

    if not manual_run and completed_occurrence(job, next_run):
        new_next = d.recompute_next() if recurring else None
        if new_next:
            scan.persist(job["id"], next_run_at=new_next)
        return False
    if kind == "cron" and not manual_run and _repair_timezone_shifted_cron(d):
        return False
    d.next_run_dt = _rearm_stale_error_recurring(d)
    if d.next_run_dt > now:
        return False

    # Only the dispatch snapshot carries this field; never infer it from a later stamp.
    job["_scheduled_instant"] = None if manual_run else scheduled_instant(job.get("next_run_at"))

    if not manual_run and kind == "cron" and _reanchor_stale_cron(d):
        return False
    grace = _jobs._compute_grace_seconds(d.schedule)
    if not manual_run and recurring and _fast_forward_missed_recurring(d, grace):
        return False
    if kind == "once":
        if _retire_expired_oneshot(d) or _oneshot_dispatch_limit_reached(job, scan):
            return False
        # Durably claim the one-shot for the DURATION of its run: a second scheduler process on the
        # same HERMES_HOME must not re-dispatch it while in flight, and advancing next_run_at by a
        # fixed window is not enough for a run that outlives a tick. The other process sees the
        # fresh claim and skips; mark_job_run() clears it. The TTL only covers a tick that DIES.
        claim = {"at": now.isoformat(), "by": _jobs._machine_id()}
        job["run_claim"] = claim
        scan.persist(job["id"], run_claim=claim)

    # Missed-run visibility: persist scheduled-vs-actual timing so separate CLI processes can show a
    # late catch-up. Recurring only — expired one-shots were retired above; manual triggers aren't
    # late.
    if not manual_run and recurring:
        lateness = max(0.0, (now - d.next_run_dt).total_seconds())
        # See #99879.
        dispatch_stamp = {
            "scheduled_at": next_run,
            "dispatched_at": now.isoformat(),
            "lateness_seconds": round(lateness, 1),
            "kind": _jobs._classify_dispatch_lateness(lateness, grace),
        }
        job["last_dispatch"] = dispatch_stamp
        # The tick advances next_run_at past this occurrence before any fire claim exists; the
        # stamp survives a process death in that window so the slot is restored, not lost.
        from cron.occurrences import pending_slot_stamp

        scan.persist(
            job["id"], last_dispatch=dispatch_stamp,
            pending_slot=pending_slot_stamp(next_run, now))
    return True


def _get_due_jobs_locked() -> List[Dict[str, Any]]:
    """Inner implementation of get_due_jobs(); must be called with _jobs_lock held."""
    raw_jobs = _jobs.load_jobs()
    scan = _DueScan(raw_jobs, _jobs._hermes_now())
    scan.needs_save = _normalize_due_scan_records(raw_jobs)
    jobs = [_jobs._apply_skill_fields(j) for j in copy.deepcopy(raw_jobs)]
    # One-shot run-claim TTL, resolved once per scan (see _oneshot_run_claim_ttl_seconds).
    run_claim_ttl = _jobs._oneshot_run_claim_ttl_seconds()

    # Retention sweep: completed one-shots are kept for inspection but must not accumulate forever.
    if _sweep_completed_oneshots(raw_jobs, scan.now, removed_ids=scan.removed):
        scan.needs_save = True
        jobs = [j for j in jobs if scan.find(j.get("id")) is not None]

    due = []
    for job in jobs:
        # Per-job containment: one malformed record must never abort the whole scan. Normalization
        # above repairs known shapes; this catches FUTURE variants so healthy siblings still
        # run/persist.
        try:
            if _jobs.is_terminal_job(job) and not _jobs._is_recoverable_error_job(job):
                continue
            if not job.get("enabled", True):
                continue
            if _jobs._has_pause_marker(job):
                _self_disable_half_paused(job, scan)
                continue
            if _evaluate_due_job(job, scan, run_claim_ttl):
                due.append(job)
        except Exception:
            logger.exception(
                "Skipping malformed cron job %r during due scan",
                job.get("name") or job.get("id") or "?")

    if scan.needs_save:
        _jobs.save_jobs(raw_jobs, removed_ids=scan.removed or None)
    return due


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
