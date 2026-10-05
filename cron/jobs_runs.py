"""Run bookkeeping and claims: claim liveness, run outcomes, next-run advance, one-shot dispatch
claims and their diagnostics, run-claim heartbeats and multi-machine fire claims.

Split out of ``cron.jobs``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_jobs`` (import-cycle breaking), so
monkeypatching ``cron.jobs.<name>`` keeps working.
"""
from __future__ import annotations

import copy
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module


# A fire_claim younger than this is a live run (heartbeat cadence is 60 s). One value
# for claiming, one-shot re-arm, and stale-error recovery so they cannot disagree.
FIRE_CLAIM_TTL_SECONDS = 300
# A hosted/webhook fire for the armed slot can arrive a few seconds before the stored
# ``next_run_at`` (the fire scheduler's clock runs ahead of ours). Claims that early still own
# the slot; only claims further ahead are off-tick manual/dashboard fires.
FIRE_CLAIM_SKEW_SECONDS = 60
def _claim_owner_is_dead(claim: Dict[str, Any]) -> bool:
    """True when the claim's ``by`` names a process on THIS host that provably no longer exists.
    ``_machine_id()`` stamps ``host:pid[:token]``; a foreign host, an explicit HERMES_MACHINE_ID,
    or any liveness-probe failure returns False (fail safe: only a proven death shortens the TTL)."""
    parts = str(claim.get("by") or "").split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return False
    try:
        import socket
        if parts[0] != socket.gethostname():
            return False
        from gateway.status import _pid_exists
        return not _pid_exists(int(parts[1]))
    except Exception:
        return False


def _claim_is_live(claim: Any, now: datetime, ttl_seconds: float) -> bool:
    """True for a well-formed claim aged within ``[0, ttl)`` whose owner is not provably dead:
    future-dated (clock/TZ skew) or malformed claims count as stale so they can never wedge a
    job, and a same-host owner pid that has exited releases the claim immediately instead of
    after the TTL (a killed ``hermes cron run`` otherwise blocks the next manual run for the
    full window with "already being fired")."""
    if not isinstance(claim, dict) or not claim.get("at"):
        return False
    claimed_at = _jobs._parse_aware(claim["at"])
    if claimed_at is None or not (0 <= (now - claimed_at).total_seconds() < ttl_seconds):
        return False
    return not _claim_owner_is_dead(claim)


def _record_run_outcome(
    job: Dict[str, Any], success: bool, error: Optional[str], delivery_error: Optional[str],
    status: Optional[str], now: str,
) -> None:
    """Stamp one completed run onto *job*: status fields, failure streak, alert markers, claims."""
    job["last_run_at"] = now
    job.pop("manual_run_at", None)
    # The transient manual-run context is single-fire: the run that just completed consumed it.
    job.pop("manual_run_prompt", None)
    delivery_failed = isinstance(delivery_error, str) and bool(delivery_error.strip())
    job["last_status"] = status or (
        "error" if not success else ("delivery_failed" if delivery_failed else "ok"))
    job["last_error"] = None if success else error
    if success:
        # Healthy run: drop the alert-once dedup markers so a FUTURE break re-alerts, and clear
        # the forward-failure stamp so it only describes CURRENT auto-fire health.
        job.pop("preflight_alerted", None)
        job.pop("last_fire_error", None)
        job["failure_streak"] = 0
    else:
        # Consecutive agent-failure streak; delivery failures do NOT count
        # (scheduler._failure_streak_nudge).
        job["failure_streak"] = int(job.get("failure_streak") or 0) + 1
    job["last_delivery_error"] = delivery_error
    # Clear both claims: the run is over, so the job is claimable again.
    job["fire_claim"] = None
    job.pop("pending_slot", None)
    if job.get("run_claim") is not None:  # keep key absence for legacy records
        job["run_claim"] = None


def _advance_after_run(job: Dict[str, Any], now: str) -> None:
    """Bump ``repeat.completed`` and recompute ``next_run_at``; retire the record as a terminal
    completion when the repeat limit is reached or a one-shot has no further run."""
    # If no next run, decide whether this is terminal completion (one-shot) or a transient failure
    # (recurring schedule couldn't compute — e.g. 'croniter' missing from the runtime env). Recurring jobs
    # must NEVER be silently disabled: that turns a missing runtime dep into "job completed" and the user's
    # schedule quietly goes off. See issue #16265.
    kind = job.get("schedule", {}).get("kind")
    # One-shot dispatch-limit guard (issue #38758): a finite one-shot claimed via claim_dispatch() but whose
    # tick died before mark_job_run could remove it will have completed >= times while still looking due
    # (last_run_at was never written, so the recovery helper re-armed it). Remove it instead of re-firing.
    repeat = job.get("repeat")
    if repeat:
        times = repeat.get("times")
        finite = times is not None and times > 0
        completed = repeat.get("completed", 0)
        # Finite one-shots were pre-claimed by claim_dispatch() (completed already incremented) —
        # do not double-count; recurring jobs and direct callers still get the increment.
        if not (kind == "once" and finite and completed > 0):
            completed += 1
            repeat["completed"] = completed
        if finite and completed >= times:
            # Limit reached: retain a terminal record instead of popping it, so the status just
            # written stays inspectable in `cronjob list`; the retention sweep prunes it later.
            _jobs._complete_job_record(job)
            return

    job["next_run_at"] = _jobs.compute_next_run(job["schedule"], now)
    if job["next_run_at"] is not None:
        if job.get("state") != "paused":
            job["state"] = "scheduled"
    elif kind in {"cron", "interval"}:
        # Recurring: transient failure (e.g. croniter missing) — disabling it would turn a missing
        # dep into "job completed" and silently drop the schedule.
        job["state"] = "error"
        if not job.get("last_error"):
            job["last_error"] = (
                "Failed to compute next run for recurring schedule (is the 'croniter' package "
                "installed in the gateway's Python env?)")
        logger.error(
            "Job '%s' (%s) could not compute next_run_at; "
            "leaving enabled and marking state=error so the job is not silently disabled.",
            job.get("name", job.get("id", "?")), kind)
    else:
        _jobs._complete_job_record(job)  # one-shot: terminal completion


def mark_job_run(
    job_id: str,
    success: bool,
    error: Optional[str] = None,
    delivery_error: Optional[str] = None,
    status: Optional[str] = None,
    *,
    expected_fire_owner: Optional[str] = None,
    model_unreachable: bool = False,
    terminal_complete: bool = False,
) -> bool:
    """Mark a job as run: update last_run_at/last_status, bump completed, recompute next_run_at,
    and retire the record as a terminal completion when the repeat limit is reached.

    ``delivery_error`` is separate from the agent error: agent succeeded but delivery failed records
    ``last_status = "delivery_failed"`` (never "ok") while ``failure_streak`` is left alone. An
    explicit ``status`` (e.g. "blocked_config") overrides the derived value. False when the fence
    can't be taken, the job is missing, or ``expected_fire_owner`` no longer holds the fire claim.

    ``model_unreachable``: this failed run never reached the model (transient network/DNS error,
    zero API calls). Recurring jobs then get a bounded automatic re-run — ``next_run_at`` is pulled
    earlier per ``cron.unreachable_retry.RETRY_DELAYS_SECONDS`` — instead of waiting a full period
    (Cowork-style; see cron/unreachable_retry.py).
    """
    def apply(jobs, _i, job):
        if expected_fire_owner is not None:
            claim = job.get("fire_claim")
            if not isinstance(claim, dict) or claim.get("by") != expected_fire_owner:
                logger.warning(
                    "mark_job_run: job_id %s fire claim owner changed; discarding stale completion",
                    job_id)
                return False
        now = _jobs._hermes_now().isoformat()
        _record_run_outcome(job, success, error, delivery_error, status, now)
        _advance_after_run(job, now)
        if success and terminal_complete:
            _jobs._complete_job_record(job)
        from cron.unreachable_retry import clear_state, plan_retry

        if not success and model_unreachable and not _jobs.is_terminal_job(job):
            plan_retry(job)
        else:
            # Any run that reached the model (either outcome) resets the re-run ladder.
            clear_state(job)
        _jobs.save_jobs(jobs)
        return True

    def locked():
        found = _jobs._with_job(job_id, apply, missing=_jobs._MISSING)
        if found is _jobs._MISSING:
            logger.warning("mark_job_run: job_id %s not found, skipping save", job_id)
            return False
        return found

    return _jobs._under_fire_fence(job_id, locked)


def _write_oneshot_diagnostic(job: Dict[str, Any], text: str, what: str) -> bool:
    """Best-effort operator-visible trace in the job's output dir; never breaks the caller."""
    try:
        _jobs.save_job_output(job.get("id", ""), text)
        return True
    except Exception as e:
        logger.debug("Failed to write %s diagnostic for job %r: %s", what, job.get("id"), e)
        return False


def _write_wedged_oneshot_diagnostic(job: Dict[str, Any]) -> None:
    """Trace for a wedged one-shot removal: dispatch was claimed but mark_job_run never ran
    (interrupted mid-run); removing it silently would leave no output, error, or record.

    A finite one-shot whose dispatch was claimed (``repeat.completed`` >= ``repeat.times``) but which never
    reached ``mark_job_run`` (``last_run_at`` is null) was interrupted mid-run — scheduler restart, gateway
    kill, or a non-Exception escape (#73973). The recovery guards remove such jobs so they stop appearing
    due, but a silent removal leaves the user with no output, no error, and no job record. Write a small
    diagnostic file into the job's output directory so the removal is observable and debuggable.
    """
    if job.get("last_run_at") is not None:
        return  # a prior run was recorded — normal completion race, not a wedge
    repeat = job.get("repeat") or {}
    claim = job.get("run_claim") or {}
    written = _write_oneshot_diagnostic(
        job,
        "# Cron job removed without producing output\n\n"
        f"- job id: {job.get('id')}\n"
        f"- name: {job.get('name')}\n"
        f"- dispatch claimed: {repeat.get('completed', '?')}/{repeat.get('times', '?')}\n"
        f"- run claimed at: {claim.get('at', 'unknown')} by {claim.get('by', 'unknown')}\n"
        f"- removed at: {_jobs._hermes_now().isoformat()}\n\n"
        "This one-shot job's dispatch was claimed, but the run never "
        "completed (`last_run_at` was never written) — the scheduler "
        "process was most likely killed or restarted mid-execution. The "
        "job has been removed to stop it re-firing; recreate it to run "
        "again.\n",
        "wedged-oneshot")
    if written:
        logger.warning(
            "Job '%s': removed without a completed run — diagnostic written to "
            "its output directory",
            job.get("name", job.get("id", "?")))


def _write_missed_oneshot_diagnostic(job: Dict[str, Any], next_run: str) -> None:
    """Trace for a never-ran one-shot retired outside the grace window (else it would just vanish).
    """
    _write_oneshot_diagnostic(
        job,
        "# Cron job removed before firing (run time outside grace window)\n\n"
        f"- job id: {job.get('id')}\n"
        f"- name: {job.get('name')}\n"
        f"- scheduled run time: {next_run}\n"
        f"- grace window: {_jobs.ONESHOT_GRACE_SECONDS}s\n"
        f"- removed at: {_jobs._hermes_now().isoformat()}\n\n"
        "This one-shot's run time is more than the grace window in the "
        "past (scheduler down past the window, host asleep, or jobs.json "
        "edited), which is outside the 'will never fire' contract "
        "enforced at create/update/resume time. The job was removed "
        "without running; recreate it (or use the Run button) to "
        "schedule it again.\n",
        "missed-oneshot")


def claim_dispatch(job_id: str) -> bool:
    """Atomically claim a finite one-shot dispatch BEFORE execution: ``repeat.completed`` is bumped
    and persisted under the jobs lock so a tick dying mid-execution cannot lose the dispatch
    (*at-most-times* instead of *at-least-once*). True if the caller may run the job; False when
    the limit is already reached. Only ``kind == "once"`` with ``repeat.times > 0`` is claimed.

    Increments ``repeat.completed`` under the cross-process jobs lock and persists the claim immediately, so
    that if the tick dies mid-execution (gateway kill, OOM, segfault, hard-timeout) the dispatch is not
    lost. This converts finite one-shot jobs from *at-least-once* to *at-most-times* semantics — a job that
    self-destructs fires at most ``repeat.times`` times instead of infinitely (issue #38758).
    """
    def apply(jobs, i, job):
        repeat = job.get("repeat") or {}
        times = repeat.get("times")
        # Recurring jobs use advance_next_run(); no/infinite repeat limit always dispatches.
        if job.get("schedule", {}).get("kind") != "once" or times is None or times <= 0:
            return True
        completed = repeat.get("completed", 0)
        label = job.get("name", job.get("id", "?"))
        if completed >= times:
            if job.get("last_run_at") is not None:
                # A prior run completed normally (mark_job_run raced this tick). Retain the terminal
                # record, as mark_job_run's repeat-limit branch does, instead of deleting the
                # status.
                _jobs._complete_job_record(job)
                _jobs.save_jobs(jobs)
                logger.info(
                    "Job '%s': dispatch limit reached (%d/%d) — marking completed",
                    label, completed, times)
                return False
            # A prior tick claimed the dispatch then died — a genuinely wedged claim. Remove it so
            # it stops appearing due, leaving an operator-visible diagnostic.
            jobs.pop(i)
            # See #73973.
            _jobs.save_jobs(jobs, removed_ids={job_id})
            _write_wedged_oneshot_diagnostic(job)
            logger.info(
                "Job '%s': dispatch limit reached (%d/%d) — removing", label, completed, times)
            return False
        # Claim this dispatch before the side effect runs.
        repeat["completed"] = completed + 1
        _jobs.save_jobs(jobs)
        logger.debug("Job '%s': claimed dispatch %d/%d", label, repeat["completed"], times)
        return True

    claimed = _jobs._with_job(job_id, apply, missing=_jobs._MISSING)
    if claimed is _jobs._MISSING:
        logger.debug(
            "claim_dispatch: job_id %s not in store — proceeding without claim "
            "(handed-in job dict; nothing to persist a claim against)",
            job_id)
        return True
    return claimed


def _refresh_claim(jobs: List[Dict[str, Any]], claim: Any, expected_owner: str) -> bool:
    """Compare-and-refresh a claim's ``at`` stamp; False unless *expected_owner* still holds it."""
    if not isinstance(claim, dict) or claim.get("by") != expected_owner:
        return False
    claim["at"] = _jobs._hermes_now().isoformat()
    _jobs.save_jobs(jobs)
    return True


def heartbeat_run_claim(job_id: str, *, expected_owner: str) -> bool:
    """Refresh a one-shot's ``run_claim`` timestamp while its run is alive, so an expired claim
    really means the claiming process died. Compare-and-refresh on ``expected_owner`` stops a stale
    runner from extending a claim another process has since taken over.

    Called periodically from the scheduler's run monitor (#62002) so a legitimately long run keeps its claim
    fresh: an expired claim then really does mean "the claiming process died", and neither another process's
    tick nor this process's own next tick will re-dispatch or stale-remove the job while the run is in
    flight. mark_job_run() clears the claim on completion.
    """
    def apply(jobs, _i, job):
        if job.get("schedule", {}).get("kind") != "once":
            return False
        return _refresh_claim(jobs, job.get("run_claim"), expected_owner)

    return _jobs._with_job(job_id, apply, False)


def clear_run_claim(job_id: str) -> bool:
    """Clear a one-shot's ``run_claim`` when dispatch itself fails: such a job never reaches
    mark_job_run, so the stale claim would block re-dispatch until the TTL expires.

    Calling this on every early-exit path restores the "the job stays due and will fire on the next healthy
    tick" invariant that the scheduler comment promises (#86522).
    """
    def apply(jobs, _i, job):
        if job.get("schedule", {}).get("kind") != "once" or job.get("run_claim") is None:
            return False  # recurring, or already cleared
        job["run_claim"] = None
        _jobs.save_jobs(jobs)
        return True

    return _jobs._with_job(job_id, apply, False)


def advance_next_runs(job_ids) -> int:
    """Batch form of :func:`advance_next_run`: one load + at most one save for the whole due set;
    one-shot/unknown ids are skipped. Returns the count advanced. Persisted once at the end, so a
    crash mid-batch re-fires the whole set on restart rather than a prefix (sub-10ms window)."""
    ids = set(job_ids)
    if not ids:
        return 0
    with _jobs._jobs_lock():
        jobs = _jobs.load_jobs()
        now = _jobs._hermes_now().isoformat()
        advanced = 0
        for job in jobs:
            if (
                job["id"] not in ids
                or (_jobs.is_terminal_job(job) and not _jobs._is_recoverable_error_job(job))
                or job.get("schedule", {}).get("kind") not in {"cron", "interval"}
            ):
                continue
            new_next = _jobs.compute_next_run(job["schedule"], now)
            if new_next and new_next != job.get("next_run_at"):
                job["next_run_at"] = new_next
                advanced += 1
        if advanced:
            _jobs.save_jobs(jobs)
        return advanced


def advance_next_run(job_id: str) -> bool:
    """Advance a recurring job's next_run_at BEFORE run_job() so a mid-run crash cannot re-fire it
    on restart (at-most-once for recurring jobs — one missed run beats a crash-loop burst).
    One-shots are left unchanged so they can retry. Returns True if next_run_at was advanced."""
    # >= 1 (not == 1): duplicate ids in a corrupted file all advance; still report the advance.
    return advance_next_runs([job_id]) >= 1


def _machine_id() -> str:
    """Claim attribution/debugging id (NOT correctness — that comes from the file lock and the
    fresh-claim check): ``HERMES_MACHINE_ID`` if set, else hostname:pid."""
    explicit = os.getenv("HERMES_MACHINE_ID", "").strip()
    if explicit:
        return explicit
    try:
        import socket
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{host}:{os.getpid()}"


def claim_job_for_fire(
    job_id: str, *, claim_ttl_seconds: int = FIRE_CLAIM_TTL_SECONDS, force: bool = False,
    manual: bool = False, return_job: bool = False,
) -> Union[bool, Dict[str, Any]]:
    """Atomically claim a job for one external 'fire' (multi-machine at-most-once); True iff THIS
    caller won (``CronScheduler.fire_due``: exactly one of N replicas runs a job). Under the
    fence + file lock: reject missing/terminal/paused jobs unless ``force`` (explicit manual
    fire, which also resumes the job atomically; external callbacks must leave it false so a
    stale callback cannot resurrect a paused job). ``manual`` = off-tick run-now without the
    resume: no occurrence stamp, so the still-pending ``next_run_at`` slot is not skipped. Lose if a claim younger than
    ``claim_ttl_seconds`` exists (the TTL lets another fire reclaim after a crash; mark_job_run
    clears the claim). Otherwise stamp ``fire_claim`` and, for recurring jobs, advance
    ``next_run_at`` so a stale re-delivery cannot re-fire."""
    def apply(jobs, _i, job):
        if _jobs.is_terminal_job(job) and not _jobs._is_recoverable_error_job(job):
            return False
        # Both enabled and pause markers must clear — a half-paused record must not claim. ``force``
        # (Trigger-now on a paused job) bypasses the gate and atomically resumes the job below.
        if not force and not _jobs.is_job_runnable(job):
            return False
        now = _jobs._hermes_now()
        if _claim_is_live(job.get("fire_claim"), now, claim_ttl_seconds):
            return False  # someone holds a fresh claim
        from cron.occurrences import completed_occurrence, scheduled_instant

        # ``manual`` (an off-tick run-now) must NOT stamp an occurrence identity: outside a
        # scheduler tick ``next_run_at`` is the NEXT occurrence, not the one being run, so
        # stamping it would make completed_occurrence() skip that slot when it arrives.
        manual_fire = force or manual or job.get("manual_run_at") == job.get("next_run_at")
        instant = None if manual_fire else scheduled_instant(job.get("next_run_at"))
        # A scheduled tick only ever fires when now >= next_run_at
        # (_evaluate_due_job returns False while the stored occurrence is still
        # in the future), so a claim arriving BEFORE the stored next occurrence
        # cannot be the tick that owns it — it is a manual / dashboard / webhook
        # fire and must stay occurrence-free. Binding it would make run_one_job
        # stamp that FUTURE instant completed in the ledger: later manual fires
        # are then refused ("Job is already being fired by the scheduler") and
        # the scheduled tick dedupe-skips its real delivery (2026-09-08 live:
        # a manual run at 19:53 consumed the next day's 19:00 occurrence).
        # A claim within FIRE_CLAIM_SKEW_SECONDS of the slot is the fire for that slot
        # (provider clock skew); dropping its identity would leave the slot unrecorded, so
        # mark_job_run recomputes the same cron slot and the misfire backstop runs it twice.
        if (instant is not None
                and datetime.fromisoformat(instant) - now >= timedelta(seconds=FIRE_CLAIM_SKEW_SECONDS)):
            instant = None
        if instant and completed_occurrence(job, instant):
            if job.get("schedule", {}).get("kind") in {"cron", "interval"}:
                nxt = _jobs.compute_next_run(job["schedule"], now.isoformat())
                if nxt:
                    job["next_run_at"] = nxt
                    _jobs.save_jobs(jobs)
            return False
        if force:
            _jobs._activate_job_record(job)
        # Per-acquisition token: a process may legitimately reclaim its own stale lease, and the
        # previous runner must not heartbeat the new claim merely because hostname + PID match.
        job["fire_claim"] = {"at": now.isoformat(), "by": f"{_machine_id()}:{uuid.uuid4().hex}"}
        # Claimed: the occurrence is now owned by a run (its ledger row + fire claim carry it).
        job.pop("pending_slot", None)
        if job.get("schedule", {}).get("kind") in {"cron", "interval"}:
            nxt = _jobs.compute_next_run(job["schedule"], now.isoformat())
            if nxt:
                job["next_run_at"] = nxt
        _jobs.save_jobs(jobs)
        return dict(copy.deepcopy(job), _scheduled_instant=instant) if return_job else True

    return _jobs._under_fire_fence(job_id, lambda: _jobs._with_job(job_id, apply, False))


def heartbeat_fire_claim(job_id: str, *, expected_owner: str) -> bool:
    """Refresh an active ``fire_claim`` without extending another owner's lease: an execution may
    outlive the TTL, and the owner check stops a stale runner from refreshing a recovered claim.
    Deliberately NOT under ``_under_fire_fence``: the run thread holds the per-job fence across
    delivery, and the fence's reentrancy is per thread, so the heartbeat thread would time out and
    report a false ownership loss. ``_jobs_lock`` already serializes the compare-and-refresh."""
    def apply(jobs, _i, job):
        return _refresh_claim(jobs, job.get("fire_claim"), expected_owner)

    return _jobs._with_job(job_id, apply, False)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
