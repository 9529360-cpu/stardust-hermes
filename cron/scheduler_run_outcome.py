"""After a run: fire-claim ownership, composing and delivering the result or failure notice,
publishing it to the conversation that created the job, and marking the job.

Split out of ``cron.scheduler``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_sched`` (import-cycle breaking), so
monkeypatching ``cron.scheduler.<name>`` keeps working.
"""
from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("cron.scheduler")  # log-record parity with the origin module


_OWNERSHIP_LOST_INTERRUPTED = "Interrupted by shutdown before terminal completion."


def _record_fire_ownership_lost(job_id: str, fire_owner: Optional[str], execution_id: str) -> None:
    """Bookkeeping after fire-claim ownership loss. A transport-level cancel (dashboard drain) is
    not a real loss — we still own the claim, so record the interruption via the owner-fenced
    terminal write instead of leaving fire_claim/last_status stale; otherwise discard."""
    if fire_owner is not None and _sched.heartbeat_fire_claim(job_id, expected_owner=fire_owner):
        _sched.mark_job_run(job_id, False, _OWNERSHIP_LOST_INTERRUPTED, expected_fire_owner=fire_owner)
        _sched.finish_execution(execution_id, success=False, error=_OWNERSHIP_LOST_INTERRUPTED)
    else:
        _sched.finish_execution(
            execution_id, success=False,
            error="Fire claim ownership lost; stale result was discarded.")


def _classify_delivery_outcome(
    *, delivery_error, should_deliver: bool, unresolved_origin: bool,
    normalized_deliver: str, incident_acked: bool, success: bool,
    delivery_queued=None, local_session_delivered: bool = False,
) -> str:
    if delivery_error:
        return "failed"
    if local_session_delivered:
        return "delivered"
    if should_deliver and delivery_queued:
        return "queued"
    if should_deliver and unresolved_origin:
        return "not_configured"
    if should_deliver and normalized_deliver != "local":
        return "delivered"
    if incident_acked and not success:
        # Failure ping withheld: operator acked this exact signature (vs. plain "suppressed").
        return "suppressed_acked"
    return "suppressed"


def _compose_run_delivery(
    job: dict, *, success: bool, error, final_response: str, output_file,
) -> tuple[str, bool, bool, bool, Optional[str]]:
    """Text to deliver for a finished run. Returns ``(deliver_content, blocked_config,
    silent_alert, incident_acked, failure_incident_id)``; ``silent_alert``: an alert-once marker
    says the operator was already told, deliver nothing."""
    err = str(error) if error else ""
    # Failed jobs always deliver, except blocked-config runs, which alert exactly ONCE.
    blocked_config_silent = _sched.BLOCKED_CONFIG_SILENT_MARKER in err
    blocked_config = blocked_config_silent or _sched.BLOCKED_CONFIG_MARKER in err
    incident_acked = False
    failure_incident_id = None
    if blocked_config and not success:
        # Bypass the generic failure summarizer (its auth/timeout heuristics would mislabel this).
        _pf_text = re.sub(r"\[blocked_config[^\]]*\]\s*", "", err).strip()
        from cron.scheduler_failure_copy import blocked_config_notice
        deliver_content = blocked_config_notice(job.get("name") or job["id"], _pf_text)
    elif success:
        deliver_content = final_response
        _sched._resolve_incidents_for_recovered_job(job)
    else:
        # Record the job+error signature once; if already acked by the operator, suppress the
        # per-run ping. Best-effort: a ledger failure never breaks delivery.
        incident_acked, failure_incident_id = _sched._upsert_incident_for_failure(
            job, error or "", output_file=output_file
        )
        if incident_acked:
            deliver_content = ""
        else:
            deliver_content = (
                _sched._summarize_cron_failure_for_delivery(job, error) + _sched._failure_streak_nudge(job)
            )
    return deliver_content, blocked_config, blocked_config_silent, incident_acked, failure_incident_id


class _FireClaimLostDuringSideEffect(Exception):
    """Raised inside a side-effect fence when the durable fire claim is no longer ours."""


class _FireOwnership:
    """Fire-claim ownership checks for one run (``owner`` is None when the job carries no claim)."""

    def __init__(self, job: dict, fire_claim_lost: Optional[_sched._CancelEventLike]):
        self.job = job
        self.fire_claim_lost = fire_claim_lost
        claim = job.get("fire_claim")
        self.owner = str(claim.get("by") or "") if isinstance(claim, dict) else None

    def side_effect_fence(self):
        if self.owner is None:
            return contextlib.nullcontext(True)
        return _sched.fire_claim_fence(self.job["id"], expected_owner=self.owner)

    def lost(self) -> bool:
        if self.fire_claim_lost is not None and self.fire_claim_lost.is_set():
            return True
        if self.owner is None:
            return False
        if _sched.self_removal_delivery_allowed(self.job["id"]):
            # The run deleted its own record; there is no claim left to re-resolve.
            return False
        try:
            if _sched.heartbeat_fire_claim(self.job["id"], expected_owner=self.owner):
                return False
        except Exception:
            logger.debug(
                "Job '%s': fire_claim ownership validation failed", self.job["id"], exc_info=True)
            return False
        if self.fire_claim_lost is not None:
            self.fire_claim_lost.set()
        return True


@dataclass
class _RunDelivery:
    """Mutable outcome of the save/compose/deliver phase, read back by the bookkeeping tail."""
    job: dict
    success: bool
    error: Optional[str]
    delivery_attempted: bool = False
    delivery_error: Optional[str] = None
    should_deliver: bool = False
    unresolved_origin: bool = False
    blocked_config: bool = False
    incident_acked: bool = False
    failure_incident_id: Optional[str] = None
    side_effect_ownership_lost: bool = False
    delivery_content: str = ""
    local_session_delivered: bool = False
    terminal_complete: bool = False


def _save_compose_deliver(
    d: _RunDelivery, fence: _FireOwnership, final_response: str, output: str, *,
    adapters, loop, verbose: bool, execution_token,
) -> None:
    """Save output, compose the notice and deliver it (both side effects run under the fire-claim
    fence; a lost claim raises ``_FireClaimLostDuringSideEffect`` for the caller)."""
    job = d.job
    with fence.side_effect_fence() as owns_output:
        if not owns_output:
            raise _FireClaimLostDuringSideEffect
        # remove_job() already deleted this job's output dir; saving would re-create an orphan.
        output_file = (
            None if _sched.self_removal_delivery_allowed(job["id"])
            else _sched.save_job_output(job["id"], output))
    if verbose and output_file is not None:
        logger.info("Output saved to: %s", output_file)

    # A shutdown-killed tool subprocess can leave a plausible final_response from truncated
    # output; force the honest "interrupted" failure path. Peek-only (consumed later).
    if d.success and _sched._is_interrupted(job["id"], execution_token):
        d.success = False
        d.error = (
            "Interrupted by gateway shutdown before the run finished "
            "(tool subprocess was killed mid-flight)."
        )

    (
        deliver_content, d.blocked_config, _silent_alert, d.incident_acked, d.failure_incident_id,
    ) = _compose_run_delivery(
        job, success=d.success, error=d.error, final_response=final_response,
        output_file=output_file)
    d.delivery_content = deliver_content
    # Whitespace-only == empty: skip delivery; the guard below marks it a soft failure.
    d.should_deliver = bool(deliver_content.strip()) and not _silent_alert
    if d.should_deliver and not d.success and job.get("_model_unreachable"):
        # No model call completed (network down, or the provider did not answer) and a bounded
        # automatic re-run will be scheduled (cron/unreachable_retry.py): hold the failure
        # notice — the re-run either
        # delivers the real result or, once the ladder is exhausted, the next failure
        # alerts normally. Mirrors Cowork's silent 5/15/30-minute re-runs.
        from cron.unreachable_retry import will_retry
        if will_retry(job):
            d.should_deliver = False
            logger.info(
                "Job '%s': suppressing failure notice — automatic re-run pending", job["id"])
    # Not a substring check: bare "SILENT"/"NO_REPLY" or a report quoting "[SILENT]" must
    # not be swallowed; bracketed-prefix / trailing-line tolerance is kept.
    if d.should_deliver and d.success and _sched._is_cron_silence_response(deliver_content):
        # Cron silence suppression — see _is_cron_silence_response. Replaces the old `SILENT_MARKER in
        # ...upper()` substring check, which both leaked bracketless near-markers ("SILENT" / "NO_REPLY")
        # and wrongly swallowed a real report that merely quoted "[SILENT]" mid-sentence (#51438, #46917).
        logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], _sched.SILENT_MARKER)
        d.should_deliver = False

    if d.should_deliver and fence.lost():
        d.should_deliver = False
        logger.warning("Job '%s': skipping delivery after fire claim ownership loss", job["id"])

    if not d.should_deliver:
        return
    d.unresolved_origin = (
        _sched._normalize_deliver_value(_sched._delivery_lane_value(job, for_failure=not d.success)) == "origin"
        and not _sched._resolve_delivery_targets(job, for_failure=not d.success)
    )
    try:
        with fence.side_effect_fence() as owns_delivery:
            if not owns_delivery:
                raise _FireClaimLostDuringSideEffect
            d.delivery_attempted = True
            d.delivery_error = _sched._deliver_result(
                job,
                deliver_content,
                adapters=adapters,
                loop=loop,
                # Failure summaries (and drift/blocked-config alerts composed into deliver_content
                # on the failure path) honor the job's failure_deliver override (NS-788).
                for_failure=not d.success,
            )
    except Exception as de:
        if isinstance(de, _FireClaimLostDuringSideEffect):
            raise
        d.delivery_error = str(de)
        logger.error("Delivery failed for job %s: %s", job["id"], de)


def _publish_local_session_completion(
    d: _RunDelivery, fence: _FireOwnership, execution_id: str,
) -> None:
    """Return a non-silent scheduled result to the local conversation that created the job.

    The result rides the same durable completion ledger and idle-turn admission path as
    background delegation/manual cron runs. This is a delivery side effect, so it is fenced
    by the job fire owner and a publish failure is recorded as a delivery failure rather than
    pretending the scheduled task itself did not run.
    """
    origin = d.job.get("local_session_origin")
    if not d.should_deliver or not isinstance(origin, dict):
        return
    source = str(origin.get("source") or "").strip().lower()
    session_id = str(origin.get("session_id") or "").strip()
    delivery_lane = _sched._normalize_deliver_value(_sched._delivery_lane_value(d.job, for_failure=not d.success))
    if source not in {"desktop", "tui"} or not session_id or delivery_lane not in {"local", "origin"}:
        return
    with fence.side_effect_fence() as owns_delivery:
        if not owns_delivery:
            raise _FireClaimLostDuringSideEffect
        try:
            from tools.async_delegation import publish_durable_completion
            name = str(d.job.get("name") or d.job.get("id") or "Scheduled task")
            publish_durable_completion(
                delegation_id=f"cron_{execution_id}",
                session_key=session_id,
                parent_session_id=session_id,
                goal=name,
                summary=d.delivery_content,
                status="completed" if d.success else "error",
                error=d.error,
                role="cron_run",
                model=d.job.get("model"),
                context=f"Scheduled cron job {d.job.get('id', '')} completed in the background.",
                event_metadata={
                    "cron_job_id": str(d.job.get("id") or ""),
                    "cron_job_name": name,
                },
            )
            d.local_session_delivered = True
            d.delivery_attempted = True
        except Exception as exc:
            local_error = f"local session delivery failed: {exc}"
            d.delivery_error = "; ".join(filter(None, (d.delivery_error, local_error)))
            logger.error("Job '%s': %s", d.job.get("id"), local_error, exc_info=True)

def _finish_interrupted_run(job: dict, execution_id: str, delivery_error: Optional[str]) -> None:
    """Shutdown already wrote last_status, so mark_job_run is skipped (a second call would skip a
    fire or auto-delete the job); an unsent notice is recorded via update_job instead."""
    if delivery_error:
        try:
            # The gateway shutdown already wrote last_status for this run, so mark_job_run is skipped below
            # — but it could not know that the notice we just tried to send never left the process (the
            # adapters were torn down first, #82232). Record the delivery failure on its own via update_job:
            # mark_job_run also advances next_run_at and the repeat counter, and running that a second time
            # for one run would skip a fire or auto-delete the job early.
            from cron.jobs import update_job
            update_job(job["id"], {"last_delivery_error": delivery_error})
        except Exception as _rec_err:
            logger.debug(
                "Failed recording delivery_error for interrupted job %s: %s", job["id"], _rec_err)
    _sched.finish_execution(
        execution_id, success=False,
        error="Interrupted by gateway shutdown before terminal completion.")


def _finish_completed_run(d: _RunDelivery, fire_owner: Optional[str], execution_id: str) -> bool:
    """mark_job_run (owner-fenced) + execution ledger row for a run that reached delivery."""
    job = d.job
    if not d.should_deliver and job.get("last_delivery_queued"):
        from cron.jobs import update_job
        update_job(job["id"], {"last_delivery_queued": None})
        job["last_delivery_queued"] = None
    mark_kwargs: dict = {"delivery_error": d.delivery_error}
    if not d.success and job.pop("_model_unreachable", False):
        # No-model-call-completed failure: schedule the Cowork-style bounded re-run
        # (cron/unreachable_retry.py) inside the same fenced store write.
        mark_kwargs["model_unreachable"] = True
    if d.success and not d.delivery_error and d.should_deliver and job.get("last_delivery_queued"):
        mark_kwargs["status"] = "delivery_queued"
    if fire_owner is not None:
        mark_kwargs["expected_fire_owner"] = fire_owner
    if d.blocked_config:
        mark_kwargs["status"] = "blocked_config"
    if d.terminal_complete:
        mark_kwargs["terminal_complete"] = True
    # A run that removed its own record has nothing left to mark; the delivery above is its result.
    marked = _sched.self_removal_delivery_allowed(job["id"]) or _sched.mark_job_run(
        job["id"], d.success, d.error, **mark_kwargs)
    if fire_owner is not None and not marked:
        _sched.finish_execution(
            execution_id, success=False,
            error="Fire claim ownership lost before terminal completion.")
        return True
    delivery_outcome = _classify_delivery_outcome(
        delivery_error=d.delivery_error,
        delivery_queued=job.get("last_delivery_queued"),
        should_deliver=d.should_deliver,
        unresolved_origin=d.unresolved_origin,
        # Read the lane the notice was actually routed through (failure_deliver on failure).
        normalized_deliver=_sched._normalize_deliver_value(_sched._delivery_lane_value(job, for_failure=not d.success)),
        incident_acked=d.incident_acked,
        success=d.success,
        local_session_delivered=d.local_session_delivered,
    )
    if delivery_outcome in ("delivered", "not_configured") and not d.success:
        # Failure ping left the process (or had a configured target): mark the incident alerted.
        _sched._mark_incident_alerted(d.failure_incident_id)
    _sched.finish_execution(
        execution_id, success=d.success, error=d.error, delivery_outcome=delivery_outcome)
    return True


def _deliver_crash_failure(
    job: dict, err_text: str, *, adapters, loop,
) -> tuple[Optional[str], str]:
    """Failure notice for a run that raised out of run_job. Returns (delivery_error, outcome)."""
    normalized_deliver = _sched._normalize_deliver_value(_sched._delivery_lane_value(job, for_failure=True))
    # Same ack gate as the normal failure delivery: acked signatures stay silent here too.
    incident_acked, failure_incident_id = _sched._upsert_incident_for_failure(job, err_text)
    if incident_acked:
        return None, "suppressed_acked"
    delivery_error = None
    try:
        delivery_error = _sched._deliver_result(
            job,
            # Same text as the normal failure delivery: this run also counts toward
            # failure_streak, so the nudge must leave through here too.
            _sched._summarize_cron_failure_for_delivery(job, err_text) + _sched._failure_streak_nudge(job),
            adapters=adapters,
            loop=loop,
            for_failure=True,
        )
    except Exception as delivery_exc:
        delivery_error = str(delivery_exc)
        logger.error("Delivery failed for job %s: %s", job["id"], delivery_exc)
    unresolved_origin = bool(
        not delivery_error
        and normalized_deliver == "origin"
        and not _sched._resolve_delivery_targets(job, for_failure=True)
    )
    delivery_outcome = _classify_delivery_outcome(
        delivery_error=delivery_error, should_deliver=True, unresolved_origin=unresolved_origin,
        normalized_deliver=normalized_deliver, incident_acked=False, success=False,
        delivery_queued=job.get("last_delivery_queued"))
    if delivery_outcome in ("delivered", "not_configured"):
        _sched._mark_incident_alerted(failure_incident_id)
    return delivery_error, delivery_outcome


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.scheduler`` re-exports from it.
from cron import scheduler as _sched  # noqa: E402
