"""Failure notices and incidents for cron runs: the one-line chat notice, the backup-provider
clause, the repeated-failure review nudge, and durable incident records.

Split out of ``cron.scheduler``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_sched`` (import-cycle breaking), so
monkeypatching ``cron.scheduler.<name>`` keeps working.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("cron.scheduler")  # log-record parity with the origin module


def _fallback_chain_phrase() -> str:
    """Backup-provider clause for a provider-failure notice: "the backups failed too" vs "none
    configured" (most installs). Fails open to the former if config can't be read — never crash
    delivery.
    """
    try:
        cfg = _sched.load_config() or {}
        chain = _sched.get_fallback_chain(cfg)
    except Exception:
        return "No backup provider succeeded either."
    if chain:
        return "No backup provider succeeded either."
    return (
        "No backup provider is configured — add one with `hermes fallback add`, "
        "or set a cron-wide default via `cron.model` + `cron.model_provider` in config.yaml."
    )


def _failure_streak_nudge(job: dict) -> str:
    """Review nudge when a recurring job keeps failing, else "". The failure message is delivered
    BEFORE mark_job_run records this run, hence stored ``failure_streak`` + 1. Threshold:
    ``cron.failure_nudge_threshold`` (default 3, 0 disables)."""
    schedule_kind = (job.get("schedule") or {}).get("kind")
    if schedule_kind not in {"cron", "interval"}:
        return ""
    try:
        cfg = _sched.load_config() or {}
        threshold = int(
            ((cfg.get("cron") or {}) if isinstance(cfg, dict) else {}).get(
                "failure_nudge_threshold", 3
            )
        )
    except Exception:
        threshold = 3
    if threshold <= 0:
        return ""
    streak = int(job.get("failure_streak") or 0) + 1  # +1 = this run
    if streak < threshold:
        return ""
    job_ref = job.get("name") or job.get("id") or "this job"
    return (
        f"\nThis job has failed {streak} runs in a row — worth a review. "
        f"Fix its prompt/config, or pause it with `hermes cron pause {job_ref}` "
        "(resume/remove also available) to stop the noise."
    )


def _summarize_cron_failure_for_delivery(job: dict, error: str | None) -> str:
    """One-line failure notice for chat delivery (full details stay in the run output).

    Deterministic scheduler/script shapes are matched first (their text can contain "timed out"
    and would otherwise be blamed on the model service); everything else goes through the shared
    ``classify_api_error`` verdict and the copy table in ``scheduler_failure_copy``."""
    from cron.scheduler_failure_copy import (
        classify_cron_failure_reason, generic_failure_notice, inactivity_notice,
        provider_failure_notice, script_timeout_notice)

    job_name = job.get("name") or job.get("id") or "cron job"
    job_id = job.get("id") or job_name
    text = (error or "unknown error").strip()
    lower = text.lower()

    # Script runner contract ("Script timed out after {n}s: {path}") — also for agent jobs with a
    # context script. Must precede provider classification so it never claims a model failure.
    # See #78503, #82460.
    if lower.startswith("script timed out"):
        return script_timeout_notice(job_name, job_id)

    # Scheduler inactivity watchdog ("idle for {n}s (limit {m}s)"): the job's OWN tool call went
    # quiet, no model service involved. Its text may still contain "timed out", so it must be
    # recognised before the classifier (field-reported: a stuck `terminal` call was blamed on the
    # provider and the operator debugged the wrong system).
    if re.search(r"idle for \d+s\s*\(limit \d+s\)", lower):
        return inactivity_notice(job_name, job_id)

    # no_agent jobs never reach a model, so provider errors are structurally impossible for them:
    # gate on job MODE before classifying, or a script's own wording ("429", "timed out") would
    # blame the wrong subsystem.
    if not job.get("no_agent"):
        notice = provider_failure_notice(
            job_name, job_id, classify_cron_failure_reason(text),
            backup_provider_phrase=_fallback_chain_phrase())
        if notice is not None:
            return notice

    # Strip exception wrappers; bound input first so a multi-KB blob can't slow the regexes.
    cleaned = re.sub(r"^(RuntimeError|Exception|ValueError|HTTPStatusError):\s*", "", text[:2000])
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    if len(cleaned) > 180:
        cleaned = cleaned[:177].rstrip() + "..."
    message = generic_failure_notice(job_name, job_id, cleaned)

    # Import-class failures (#95294 part 3): a long-lived gateway whose checkout was updated
    # underneath it (interrupted `hermes update`, manual git pull) serves MIXED modules and every
    # agent cron job dies with `cannot import name X`. The error reads like a code bug, so APPEND
    # cause + fix — never replace the raw error, which carries the failing symbol. Fail-safe: skew
    # is None on non-git/no-fingerprint; no_agent jobs excluded (a fresh subprocess resolves
    # imports against disk, so its ImportError is the script's own problem).
    if not job.get("no_agent") and re.search(
        r"cannot import name|modulenotfounderror|importerror", lower
    ):
        try:
            skew = _sched._detect_gateway_code_skew()
        except Exception:
            skew = None  # delivery must never die on a diagnostics probe
        if skew is not None:
            boot_rev, disk_rev = skew
            message += (
                f" Likely cause: the gateway is running stale code (booted "
                f"on {boot_rev}, disk is at {disk_rev}) — run "
                "`hermes gateway restart` to fix it."
            )

    return message


def _upsert_incident_for_failure(
    job: dict, error: str, *, output_file: Optional[Any] = None
) -> tuple[bool, Optional[str]]:
    """Record a durable failure incident (grouped by job + error signature). Returns
    ``(acked, incident_id)``; acked=True when the signature's incident is already ``closed`` ->
    suppress the per-run ping. Store errors log at debug; the caller delivers as if none existed."""
    try:
        from cron.incidents import get_incident, upsert_incident

        incident_id, _is_new = upsert_incident(
            job["id"], str(error or ""), job_name=job.get("name"), output_file=output_file)
        incident = get_incident(incident_id)
        acked = bool(incident and incident.get("state") == "closed")
        return acked, incident_id
    except Exception as exc:
        logger.debug(
            "Incident store unavailable for job %s (delivery unaffected): %s",
            job["id"], exc)
        return False, None


def _resolve_incidents_for_recovered_job(job: dict) -> None:
    """Best-effort: a successful run marks the job's open incidents ``resolved`` (never touches an
    operator ``closed`` ack). Store errors log at debug; delivery is unaffected."""
    try:
        from cron.incidents import close_incidents_for_recovered_job

        close_incidents_for_recovered_job(job["id"])
    except Exception as exc:
        logger.debug("Incident store unavailable for job %s (delivery unaffected): %s", job["id"], exc)


def _mark_incident_alerted(incident_id: Optional[str]) -> None:
    """Best-effort: mark incident ``alerted`` (no-op for closed; never resurrects an acked one)."""
    if not incident_id:
        return
    try:
        from cron.incidents import set_incident_state

        set_incident_state(incident_id, "alerted")
    except Exception as exc:
        logger.debug("Failed marking incident %s alerted: %s", incident_id, exc)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.scheduler`` re-exports from it.
from cron import scheduler as _sched  # noqa: E402
