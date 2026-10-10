"""Creating and editing job records: field normalizers, provider/model snapshots, create, get,
resolve, list, update, re-snapshot, pause, resume, trigger, re-arm, remove and alert flags.

Split out of ``cron.jobs``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_jobs`` (import-cycle breaking), so
monkeypatching ``cron.jobs.<name>`` keeps working.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module


def _normalize_workdir(workdir: Optional[str]) -> Optional[str]:
    """Workdir -> absolute path, or None when empty. ``~`` expands; relative paths are rejected
    (cron runs detached from any cwd); must be an existing dir now but is deliberately NOT
    re-checked at run time (scheduler falls back with a warning). ValueError when invalid."""
    if workdir is None:
        return None
    raw = str(workdir).strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise ValueError(
            f"Cron workdir must be an absolute path (got {raw!r}). "
            f"Cron jobs run detached from any shell cwd, so relative paths are ambiguous.")
    resolved = expanded.resolve()
    if not resolved.exists():
        raise ValueError(f"Cron workdir does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Cron workdir is not a directory: {resolved}")
    return str(resolved)


def _resolve_default_model_snapshot() -> Optional[str]:
    """Default model resolved as the ticker's ``run_job`` does, so unpinned jobs can snapshot it and
    keep running on it after a later swap. ``None`` on missing config or failure ("no snapshot")."""
    try:
        from hermes_cli.config_effective import load_user_config_effective

        cfg_path = _jobs.get_hermes_home() / "config.yaml"
        if not cfg_path.exists():
            return None
        cfg = load_user_config_effective(cfg_path)
        cron_cfg = cfg.get("cron") or {}
        if isinstance(cron_cfg, dict):
            cron_model = cron_cfg.get("model")
            if isinstance(cron_model, str) and cron_model.strip():
                return cron_model.strip()
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, dict):
            model_cfg = model_cfg.get("default") or model_cfg.get("model")
        return model_cfg.strip() or None if isinstance(model_cfg, str) else None
    except Exception:
        return None


def _normalize_job_optional_text(
    value: Any, *, strip_trailing_slash: bool = False
) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return (value.strip().rstrip("/") if strip_trailing_slash else value.strip()) or None


def _normalize_base_url(value: Any) -> Optional[str]:
    return _normalize_job_optional_text(value, strip_trailing_slash=True)


def _normalize_optional_bool(value: Any) -> Optional[bool]:
    """Strict optional bool for durable opt-in job behavior."""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Expected boolean value, got {value!r}.")
    return value


def _normalize_str_list(items: Any) -> Optional[List[str]]:
    """Non-blank stripped items of *items*, or None when nothing remains."""
    return [str(j).strip() for j in items if str(j).strip()] or None


def _normalize_context_from(value: Any) -> Optional[List[str]]:
    """Accept a job id or a list of ids; anything else is None."""
    if isinstance(value, str):
        value = [value]
    return _normalize_str_list(value) if isinstance(value, list) else None


def _normalize_failure_deliver(value: Any) -> Optional[str]:
    """failure_deliver shares deliver's value grammar; flatten str/list like the tool layer's
    _normalize_deliver_param for direct create_job callers. Semantic validation happens at
    resolution time via the shared deliver path."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(p).strip() for p in value if str(p).strip()) or None
    return _normalize_job_optional_text(value)


def _normalize_local_session_origin(value: Any) -> Optional[Dict[str, str]]:
    """Trusted local conversation return route captured by the cron tool.

    The job store is profile-local, so the durable session id + local surface are sufficient;
    UI tab ids are intentionally excluded because they die when the window closes.
    """
    if not isinstance(value, dict):
        return None
    session_id = str(value.get("session_id") or "").strip()
    source = str(value.get("source") or "").strip().lower()
    if not session_id or source not in {"desktop", "tui"}:
        return None
    return {"session_id": session_id, "source": source}


def _normalize_job_approval_mode(value: Any) -> Optional[str]:
    """Normalize a durable job-scoped approval posture.

    None/blank/inherit keeps the historical profile-wide approvals.cron_mode
    behavior. approve delegates approval authority to this job's future runs;
    deny explicitly narrows it. The scheduler still applies hardline blocks and
    explicit user deny rules before this recoverable approval layer.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text == "inherit":
        return None
    if text not in {"approve", "deny"}:
        raise ValueError(
            f"Invalid approval_mode {value!r}. Valid values: inherit, approve, deny.")
    return text

def _normalize_reasoning_effort(value: Any) -> Optional[str]:
    """Spelling-only validation via the shared parser (cron knob never stricter/looser than
    config.yaml); model capability is deliberately NOT checked (model unknowable at create time,
    transports clamp at send time). None for unset, lowercase level, or ValueError."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    from hermes_constants import parse_reasoning_effort

    if parse_reasoning_effort(text) is None:
        raise ValueError(
            f"Invalid reasoning_effort {value!r}. Valid levels: "
            "none, minimal, low, medium, high, xhigh, max, ultra "
            "(empty string clears the override).")
    if text in {"false", "disabled"}:
        return "none"
    return text


# Normalizers for create_job (all fields) / update_job (present fields). Invalid values raise BEFORE
# storing.
_CREATE_FIELD_NORMALIZERS: Dict[str, Callable[[Any], Any]] = {
    "model": _normalize_job_optional_text,
    "provider": _normalize_job_optional_text,
    "base_url": _normalize_base_url,
    "script": _normalize_job_optional_text,
    "monitor_script": _normalize_job_optional_text,
    "monitor_url": _normalize_job_optional_text,
    "enabled_toolsets": lambda v: _normalize_str_list(v) if v else None,
    "workdir": _normalize_workdir,
    "no_agent": bool,
    "context_from": _normalize_context_from,
    "failure_deliver": _normalize_failure_deliver,
    "handoff_context": _normalize_job_optional_text,
    "approval_mode": _normalize_job_approval_mode,
    "stop_when_done": _normalize_optional_bool,
}
_UPDATE_FIELD_NORMALIZERS: Dict[str, Callable[[Any], Any]] = {
    "approval_mode": _normalize_job_approval_mode,
    "stop_when_done": _normalize_optional_bool,
    "handoff_context": _normalize_job_optional_text,
    "workdir": lambda v: None if v in {None, "", False} else _normalize_workdir(v),
    "monitor_script": _normalize_job_optional_text,
    "monitor_url": _normalize_job_optional_text,
    "reasoning_effort": _normalize_reasoning_effort,
}


def _compute_provider_model_snapshots(
    *, provider: Any, model: Any, base_url: Any, no_agent: Any,
) -> Tuple[Optional[str], Optional[str]]:
    """Snapshot unpinned provider/model resolution: the scheduler runs the job on this snapshot after
    a later global switch instead of silently changing spend. Pinned axes and no-agent jobs carry no
    snapshot."""
    normalized_provider = _normalize_job_optional_text(provider)
    normalized_model = _normalize_job_optional_text(model)
    normalized_base_url = _normalize_base_url(base_url)
    if bool(no_agent):
        return None, None

    provider_snapshot: Optional[str] = None
    model_snapshot: Optional[str] = None
    if normalized_provider is None:
        with contextlib.suppress(Exception):
            from hermes_cli.runtime_provider import resolve_runtime_provider

            runtime_kwargs = {"requested": None}
            # Delegate all rate-limit / 5xx retry to hermes's outer conversation loop, which honors
            # Retry-After. The SDK default (max_retries=2) uses its own 1-2s backoff that ignores
            # Retry-After and double-retries inside our loop — burning request slots against a bucket that
            # won't refill for minutes. (#26293)
            if normalized_base_url:
                runtime_kwargs["explicit_base_url"] = normalized_base_url
            snap = resolve_runtime_provider(**runtime_kwargs)
            provider_snapshot = str(snap.get("provider") or "").strip().lower() or None
    if normalized_model is None:
        with contextlib.suppress(Exception):
            model_snapshot = _resolve_default_model_snapshot() or None
    return provider_snapshot, model_snapshot


def _normalized_inference_axes(
    job: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Return the stored inference-routing fields in their semantic form."""
    return (
        _normalize_job_optional_text(job.get("provider")),
        _normalize_job_optional_text(job.get("model")), _normalize_base_url(job.get("base_url")),
        bool(job.get("no_agent")),
    )


def _validate_job_mode_invariants(
    monitor_script: Optional[str],
    monitor_url: Optional[str],
    no_agent: bool,
    script: Optional[str],
    stop_when_done: bool = False,
) -> None:
    """Execution-mode invariants shared by create_job and update_job (no bypass via the update
    door)."""
    if monitor_script and monitor_url:
        raise ValueError(
            "monitor_script and monitor_url are mutually exclusive — a job "
            "can only have one monitor source.")
    if (monitor_script or monitor_url) and no_agent:
        raise ValueError(
            "monitor_script/monitor_url cannot be combined with no_agent=True — "
            "the whole point of a monitor job is to suppress or wake the AGENT "
            "based on source changes. Use a plain no_agent script job instead.")
    if no_agent and not script:
        raise ValueError(_jobs.NO_AGENT_WITHOUT_SCRIPT_ERROR)
    if no_agent and stop_when_done:
        raise ValueError(
            "stop_when_done requires an agent run; it cannot be combined with no_agent=True.")


def _oneshot_past_grace_error(run_at: Any) -> ValueError:
    return ValueError(
        f"Requested one-shot time {run_at} is more than "
        f"{_jobs.ONESHOT_GRACE_SECONDS}s in the past and cannot be scheduled.")


def _next_run_or_reject_past_oneshot(
    parsed_schedule: Dict[str, Any], label: str, fallback_run_at: Any, what: str,
) -> Optional[str]:
    """``compute_next_run`` that raises (after a warning log) for a one-shot outside the grace
    window, so a ghost job with ``next_run_at=None`` can never be stored."""
    next_run_at = _jobs.compute_next_run(parsed_schedule)
    if parsed_schedule.get("kind") == "once" and next_run_at is None:
        run_at = parsed_schedule.get("run_at") or fallback_run_at
        logger.warning(
            "Rejecting one-shot cron job %s'%s': run_at %s is outside the %ss grace window",
            what, label, run_at, _jobs.ONESHOT_GRACE_SECONDS)
        raise _oneshot_past_grace_error(run_at)
    return next_run_at


def create_job(
    prompt: Optional[str],
    schedule: str,
    name: Optional[str] = None,
    repeat: Optional[int] = None,
    deliver: Optional[str] = None,
    origin: Optional[Dict[str, Any]] = None,
    local_session_origin: Optional[Dict[str, Any]] = None,
    source_suggestion_id: Optional[str] = None,
    skill: Optional[str] = None,
    skills: Optional[List[str]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    script: Optional[str] = None,
    context_from: Optional[Union[str, List[str]]] = None,
    enabled_toolsets: Optional[List[str]] = None,
    workdir: Optional[str] = None,
    no_agent: bool = False,
    attach_to_session: Optional[bool] = None,
    handoff_context: Optional[str] = None,
    stop_when_done: Optional[bool] = None,
    monitor_script: Optional[str] = None,
    monitor_url: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    failure_deliver: Optional[str] = None,
    paused: bool = False,
    paused_reason: Optional[str] = None,
    approval_mode: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create a new cron job and return the stored record.

    dry_run runs every check below and returns the record that would be stored, without saving it
    (the caller registers nothing either). Its ``id`` is a placeholder and must not be used.

    deliver defaults to "origin" when ``origin`` is given, else "local"; repeat None = forever.
    script: stdout is injected as prompt context, or with ``no_agent=True`` IS the job (stdout
    delivered verbatim, requires ``script``). context_from: job id(s) whose latest output is
    injected. workdir: absolute cwd for tools/scripts. monitor_script/monitor_url: cheap monitor
    source run FIRST each tick; unchanged output suppresses the agent run (mutually exclusive,
    incompatible with ``no_agent``). reasoning_effort: per-job pin; capability NOT validated."""
    local_session_origin = _normalize_local_session_origin(local_session_origin)
    source_suggestion_id = _normalize_job_optional_text(source_suggestion_id)
    if not isinstance(paused, bool):
        raise ValueError("paused must be a boolean.")
    if paused_reason is not None and not isinstance(paused_reason, str):
        raise ValueError("paused_reason must be a string.")
    if paused_reason is not None and not paused:
        raise ValueError("paused_reason requires paused=True.")
    parsed_schedule = _jobs.parse_schedule(schedule)
    # Normalize repeat: treat 0 or negative values as None (infinite). String forms
    # ('forever'/'once'/numeric) coerce via normalize_repeat_value — the shared chokepoint with update paths
    # (#66824/#64520/#7142/#71987/#95706).
    repeat = _jobs.normalize_repeat_value(repeat)
    if parsed_schedule["kind"] == "once" and repeat is None:
        repeat = 1
    if deliver is None:
        deliver = "origin" if origin else "local"
    job_id = uuid.uuid4().hex[:12]
    now = _jobs._hermes_now().isoformat()

    raw = locals()
    f = {key: norm(raw[key]) for key, norm in _CREATE_FIELD_NORMALIZERS.items()}
    normalized_skills = _jobs._normalize_skill_list(skill, skills)
    normalized_attach = attach_to_session if isinstance(attach_to_session, bool) else None
    normalized_reasoning_effort = _normalize_reasoning_effort(reasoning_effort)

    _validate_job_mode_invariants(
        f["monitor_script"], f["monitor_url"], f["no_agent"], f["script"],
        bool(f["stop_when_done"]))
    prompt_text = _jobs._coerce_job_text(prompt).strip()
    if not prompt_text and not f["script"] and not normalized_skills:
        raise ValueError(_jobs.EMPTY_PAYLOAD_ERROR)
    # Reject gateway-lifecycle commands (respawn loops) here, not just in the CLI: covers the tool.
    from cron.lifecycle_guard import check_gateway_lifecycle
    check_gateway_lifecycle(prompt_text, f["script"])

    label_source = (
        prompt_text
        or (normalized_skills[0] if normalized_skills else None)
        or (f["script"] if f["no_agent"] else None)
        or "cron job"
    )
    name = name or label_source[:50].strip()
    provider_snapshot, model_snapshot = _compute_provider_model_snapshots(
        provider=f["provider"], model=f["model"], base_url=f["base_url"], no_agent=f["no_agent"])
    next_run_at = _next_run_or_reject_past_oneshot(parsed_schedule, name, schedule, "")

    job = {
        "id": job_id,
        "name": name,
        "prompt": prompt_text,
        "skills": normalized_skills,
        "skill": normalized_skills[0] if normalized_skills else None,
        "model": f["model"],
        "provider": f["provider"],
        "provider_snapshot": provider_snapshot,
        "model_snapshot": model_snapshot,
        "base_url": f["base_url"],
        "script": f["script"],
        "no_agent": f["no_agent"],
        "monitor_script": f["monitor_script"],
        "monitor_url": f["monitor_url"],
        "monitor_state": None,
        "context_from": f["context_from"],
        "schedule": parsed_schedule,
        "schedule_display": parsed_schedule.get("display", schedule),
        "repeat": {"times": repeat, "completed": 0},  # times None = forever
        "enabled": not paused,
        "state": "paused" if paused else "scheduled",
        "paused_at": now if paused else None,
        "paused_reason": ((paused_reason or "").strip() or "Created paused; awaiting operator approval.") if paused else None,
        "created_at": now,
        "next_run_at": None if paused else next_run_at,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        # Targets acked without message_id/raw_response (accepted but UNVERIFIED).
        "last_delivery_unverified": None,
        "failure_streak": 0,
        "deliver": deliver,
        "origin": origin,  # Tracks where job was created for "origin" delivery
        "enabled_toolsets": f["enabled_toolsets"],
        "workdir": f["workdir"],
    }
    # Optional keys are persisted only when explicitly set: an absent key falls back to global
    # config (attach/reasoning) or to ``deliver`` (failure_deliver), byte-identical to pre-feature
    # jobs. local_session_origin is an internal return route, never a user-editable delivery target.
    for key, value in (
        ("attach_to_session", normalized_attach), ("reasoning_effort", normalized_reasoning_effort),
        ("failure_deliver", f["failure_deliver"]), ("local_session_origin", local_session_origin),
        ("source_suggestion_id", source_suggestion_id), ("approval_mode", f["approval_mode"]),
        ("handoff_context", f["handoff_context"] if normalized_attach is True else None),
        ("stop_when_done", True if f["stop_when_done"] else None),
    ):
        if value is not None:
            job[key] = value

    if dry_run:
        return job
    with _jobs._jobs_lock():
        _jobs.save_jobs(_jobs.load_jobs() + [job])
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    job = next((j for j in _jobs.load_jobs() if j["id"] == job_id), None)
    return _jobs._normalize_job_record(job) if job is not None else None


class AmbiguousJobReference(LookupError):
    """Raised when a job name matches more than one job."""

    def __init__(self, ref: str, matches: List[Dict[str, Any]]):
        self.ref = ref
        self.matches = matches
        ids = ", ".join(m["id"] for m in matches)
        super().__init__(
            f"Job name '{ref}' is ambiguous — matches {len(matches)} jobs: {ids}. "
            f"Use the job ID instead.")


def resolve_job_ref(ref: str) -> Optional[Dict[str, Any]]:
    """Resolve an ID or name to a job record. Exact ID wins, then case-insensitive name; an
    ambiguous name raises AmbiguousJobReference rather than silently picking one."""
    if not ref:
        return None
    jobs = _jobs.load_jobs()
    by_id = next((j for j in jobs if j["id"] == ref), None)
    if by_id is not None:
        return _jobs._normalize_job_record(by_id)
    ref_lower = ref.lower()
    name_matches = [j for j in jobs if (j.get("name") or "").lower() == ref_lower]
    if not name_matches:
        return None
    if len(name_matches) > 1:
        raise AmbiguousJobReference(ref, [_jobs._normalize_job_record(j) for j in name_matches])
    return _jobs._normalize_job_record(name_matches[0])


def list_jobs(include_disabled: bool = False) -> List[Dict[str, Any]]:
    """List all jobs, optionally including disabled ones."""
    jobs = [_jobs._normalize_job_record(j) for j in _jobs.load_jobs()]
    if not include_disabled:
        jobs = [j for j in jobs if j.get("enabled", True)]
    try:
        from cron.executions import latest_executions

        latest = latest_executions([job.get("id", "") for job in jobs])
    except Exception:
        latest = {}
    for job in jobs:
        job["latest_execution"] = latest.get(job.get("id", ""))
    return jobs


def _reject_terminal_activation(job: Dict[str, Any], updated: Dict[str, Any], job_id: str) -> None:
    """A genuinely terminal job cannot be reactivated through update_job (use cron resume)."""
    if (
        _jobs.is_terminal_job(job)
        and not _jobs._is_recoverable_error_job(job)
        and (
            updated.get("state") not in {"completed", "error"}
            or updated.get("enabled") is True
            or updated.get("next_run_at") is not None
        )
    ):
        raise ValueError(
            f"Cannot activate terminal cron job '{job.get('name', job_id)}' "
            "through update_job; use cron resume --run-now or --at.")


def _normalize_job_updates(job: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Normalize updates in place like create_job; invalid values raise BEFORE the merge. ``repeat``
    accepts the stored dict or a bare value (coerced, completed counter preserved)."""
    for key, norm in _UPDATE_FIELD_NORMALIZERS.items():
        if key in updates:
            updates[key] = norm(updates[key])
    if "repeat" in updates:
        _rp = updates["repeat"]
        completed = (job.get("repeat") or {}).get("completed", 0)
        if isinstance(_rp, dict):
            _rp = dict(_rp)
            _rp["times"] = _jobs.normalize_repeat_value(_rp.get("times"))
            _rp.setdefault("completed", completed)
            updates["repeat"] = _rp
        else:
            updates["repeat"] = {"times": _jobs.normalize_repeat_value(_rp), "completed": completed}


def _rederive_repeat_for_schedule_change(
    job: Dict[str, Any], updates: Dict[str, Any]
) -> None:
    """Re-derive the ``repeat`` default when a schedule update flips the kind.

    ``create_job`` derives it from the schedule kind (once -> 1, recurring -> forever); the update
    path must honour the same contract, otherwise a one-shot turned recurring keeps its ``times=1``
    budget and retires after one fire, while a recurring job turned one-shot never completes. An
    explicit ``repeat`` in the same update wins; a same-kind schedule edit leaves ``repeat`` alone.
    """
    if "schedule" not in updates or "repeat" in updates:
        return
    new_schedule = updates["schedule"]
    if isinstance(new_schedule, str):
        new_schedule = _jobs.parse_schedule(new_schedule)
        updates["schedule"] = new_schedule
    old_kind = (job.get("schedule") or {}).get("kind")
    new_kind = new_schedule.get("kind")
    if old_kind == new_kind:
        return
    repeat = dict(job.get("repeat") or {})
    times = repeat.get("times")
    if new_kind == "once" and times is None:
        repeat["times"] = 1
    elif new_kind != "once" and old_kind == "once" and times == 1:
        repeat["times"] = None
    else:
        return
    repeat.setdefault("completed", 0)
    updates["repeat"] = repeat


def _apply_schedule_update(updated: Dict[str, Any], updates: Dict[str, Any], job_id: str) -> None:
    """Parse a string schedule, refresh ``schedule_display`` and (unless paused) ``next_run_at``."""
    updated_schedule = updated["schedule"]
    if isinstance(updated_schedule, str):
        updated_schedule = _jobs.parse_schedule(updated_schedule)
        updated["schedule"] = updated_schedule
    updated["schedule_display"] = updates.get(
        "schedule_display", updated_schedule.get("display", updated.get("schedule_display")))
    if updated.get("state") != "paused":
        updated["next_run_at"] = _next_run_or_reject_past_oneshot(
            updated_schedule, updated.get("name", job_id), updated_schedule, "update ")


def _fill_missing_next_run(updated: Dict[str, Any]) -> None:
    """An enabled, unpaused record must never persist without ``next_run_at`` (it would never fire).
    """
    if (
        not updated.get("enabled", True)
        or updated.get("state") == "paused"
        or updated.get("next_run_at")
    ):
        return
    next_run = _jobs.compute_next_run(updated["schedule"])
    if next_run is None and updated["schedule"].get("kind") == "once":
        run_at = updated["schedule"].get("run_at", "unknown")
        raise ValueError(
            f"Requested one-shot time {run_at} is in the past "
            f"(grace window: {_jobs.ONESHOT_GRACE_SECONDS}s) and cannot be scheduled.")
    updated["next_run_at"] = next_run


def update_job(job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a job by ID, refreshing derived schedule fields when needed."""
    # ``id`` is a path component under OUTPUT_DIR — changing it would leak path-escape values.
    bad_fields = _jobs._IMMUTABLE_JOB_FIELDS.intersection(updates or {})
    if bad_fields:
        raise ValueError(f"Cron job field(s) cannot be updated: {', '.join(sorted(bad_fields))}")

    def apply(jobs, i, job):
        # A local return route represents the creation-time implicit/origin delivery choice.
        # Any later explicit non-origin delivery choice supersedes it. This is done in the
        # generic update path so REST/CLI/dashboard edits cannot leave a hidden second target.
        if "deliver" in updates and str(updates.get("deliver") or "").strip().lower() != "origin":
            updates["local_session_origin"] = None
        _rederive_repeat_for_schedule_change(job, updates)
        _normalize_job_updates(job, updates)
        previous_inference_axes = _normalized_inference_axes(job)
        updated = _jobs._apply_skill_fields({**job, **updates})
        if updated.get("local_session_origin") is None:
            updated.pop("local_session_origin", None)
        # inherit normalizes to None and is represented by absence so old
        # jobs stay byte-compatible and continue following approvals.cron_mode.
        if updated.get("approval_mode") is None:
            updated.pop("approval_mode", None)
        if not updated.get("stop_when_done"):
            updated.pop("stop_when_done", None)
        # Hidden session handoff belongs only to explicitly attached jobs. A failed
        # explicit refresh stores no stale transcript snapshot.
        if updated.get("attach_to_session") is not True or updated.get("handoff_context") is None:
            updated.pop("handoff_context", None)
        _reject_terminal_activation(job, updated, job_id)
        # Re-check on the MERGED record; scoped to changed fields so legacy records keep loading.
        if {"monitor_script", "monitor_url", "no_agent", "script", "stop_when_done"}.intersection(updates):
            _validate_job_mode_invariants(
                updated.get("monitor_script") or None,
                updated.get("monitor_url") or None,
                bool(updated.get("no_agent")),
                _normalize_job_optional_text(updated.get("script")),
                bool(updated.get("stop_when_done")))
        if any(k in updates for k in _jobs._PAYLOAD_FIELDS) and _jobs.job_payload_is_empty(updated):
            raise ValueError(_jobs.EMPTY_PAYLOAD_ERROR)
        inference_fields_changed = bool(
            {"provider", "model", "base_url", "no_agent"}.intersection(updates)
        ) and _normalized_inference_axes(updated) != previous_inference_axes

        if "schedule" in updates:
            _apply_schedule_update(updated, updates, job_id)
        if {"schedule", "next_run_at", "enabled", "state"}.intersection(updates):
            # An explicit schedule/lifecycle rewrite supersedes any occurrence the dispatcher
            # left unclaimed — pause/resume/edit must not resurrect a slot from before the edit.
            updated.pop("pending_slot", None)
        if inference_fields_changed:
            snapshots = _compute_provider_model_snapshots(
                provider=updated.get("provider"),
                model=updated.get("model"),
                base_url=updated.get("base_url"),
                no_agent=updated.get("no_agent"))
            updated["provider_snapshot"], updated["model_snapshot"] = snapshots
        _fill_missing_next_run(updated)
        _reject_terminal_activation(job, updated, job_id)
        jobs[i] = updated
        _jobs.save_jobs(jobs)
        return _jobs._normalize_job_record(updated)

    return _jobs._with_job(job_id, apply)


def resnapshot_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Refresh provider/model snapshots for a job's UNPINNED axes to the
    current global resolution.

    This is the "adopt the current global default" companion to pinning
    (#44585). Where pinning a job (``provider=... model=...``) makes it stop
    tracking the global default forever, ``resnapshot_job`` re-captures the
    current resolution so an unpinned job follows the user's deliberately
    changed default — while remaining unpinned and tracking future changes.

    Semantics:
      - Pinned axes (job has an explicit provider/model) keep their snapshot
        None and are left untouched.
      - no_agent script jobs carry no snapshot and are left untouched.
      - If the current resolution fails, the previous snapshot is left in
        place (fail-open, matching create_job semantics).

    Makes no inference call — it only recomputes the snapshot string from
    config. Returns the normalized updated job, or None if not found.
    """
    job = _jobs.resolve_job_ref(job_id)
    if not job:
        return None
    provider_snapshot, model_snapshot = _compute_provider_model_snapshots(
        provider=job.get("provider"),
        model=job.get("model"),
        base_url=job.get("base_url"),
        no_agent=job.get("no_agent"),
    )
    jobs = _jobs.load_jobs()
    for i, stored in enumerate(jobs):
        if stored["id"] != job["id"]:
            continue
        jobs[i]["provider_snapshot"] = provider_snapshot
        jobs[i]["model_snapshot"] = model_snapshot
        _jobs.save_jobs(jobs)
        return _jobs._normalize_job_record(jobs[i])
    return None


def resnapshot_all_unpinned() -> List[Dict[str, Any]]:
    """Refresh provider/model snapshots for every job that has any unpinned
    axis, adopting the current global resolution for each.

    Skips no_agent jobs and jobs pinned on all inference axes (nothing
    unpinned to refresh). Equivalent to calling ``resnapshot_job`` for each
    eligible job. Returns the list of updated jobs.
    """
    updated: List[Dict[str, Any]] = []
    jobs = _jobs.load_jobs()
    changed = False
    for job in jobs:
        if bool(job.get("no_agent")):
            continue
        if job.get("provider") and job.get("model"):
            # Pinned on every axis — nothing unpinned to refresh.
            continue
        provider_snapshot, model_snapshot = _compute_provider_model_snapshots(
            provider=job.get("provider"),
            model=job.get("model"),
            base_url=job.get("base_url"),
            no_agent=job.get("no_agent"),
        )
        job["provider_snapshot"] = provider_snapshot
        job["model_snapshot"] = model_snapshot
        changed = True
        updated.append(_jobs._normalize_job_record(job))
    if changed:
        _jobs.save_jobs(jobs)
    return updated


def pause_job(job_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pause a job without deleting it. Accepts a job ID or name."""
    job = _jobs.resolve_job_ref(job_id)
    if not job:
        return None
    return _jobs.update_job(job["id"], {
        "enabled": False,
        "state": "paused",
        "paused_at": _jobs._hermes_now().isoformat(),
        "paused_reason": reason,
    })


def resume_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Resume a paused job and compute the next future run from now. Accepts a job ID or name."""
    job = _jobs.resolve_job_ref(job_id)
    if not job:
        return None
    next_run_at = _jobs.compute_next_run(job["schedule"])
    if next_run_at is None and job["schedule"].get("kind") == "once":
        run_at = job["schedule"].get("run_at", "unknown")
        raise ValueError(
            f"Cannot resume: one-shot time {run_at} is in the past "
            f"(grace window: {_jobs.ONESHOT_GRACE_SECONDS}s) and will never fire.")
    return _jobs.update_job(job["id"], {
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "next_run_at": next_run_at,
    })


def trigger_job(job_id: str, extra_prompt: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Schedule a job for the next tick (ID or name). ``extra_prompt`` is stamped as
    ``manual_run_prompt`` for that single fire only; ``mark_job_run`` clears it."""
    job = _jobs.resolve_job_ref(job_id)
    if not job:
        return None
    if _jobs.is_terminal_job(job):
        name = job.get("name", job_id)
        raise ValueError(
            f"Cannot run: job '{name}' is {job.get('state')} (terminal). "
            f"Create a new occurrence with 'hermes cron resume {name} "
            "--run-now' or '--at <ISO-8601>'.")
    manual_run_at = _jobs._hermes_now().isoformat()
    return _jobs.update_job(job["id"], {
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "next_run_at": manual_run_at,
        # Run-now intent, so cron expression/TZ repair guards don't treat it as stale state.
        "manual_run_at": manual_run_at,
        "manual_run_prompt": (extra_prompt or None),
    })


_REARM_RECURRING_ERROR = (
    "Cannot re-arm recurring jobs: re-arm is one-shot-only; use plain resume or cron run."
)


def rearm_oneshot(job_id: str, run_at: Any) -> Optional[Dict[str, Any]]:
    """Re-arm a completed one-shot as an explicit new occurrence."""
    job_ref = _jobs.resolve_job_ref(job_id)
    if not job_ref:
        return None
    if isinstance(run_at, datetime):
        run_at = run_at.isoformat()
    parsed_schedule = _jobs.parse_schedule(str(run_at))
    if parsed_schedule.get("kind") != "once":
        raise ValueError(_REARM_RECURRING_ERROR)
    next_run_at = _jobs.compute_next_run(parsed_schedule)
    if next_run_at is None:
        raise _oneshot_past_grace_error(parsed_schedule.get("run_at") or run_at)

    def apply(jobs, _i, job):
        now = _jobs._hermes_now()
        if _jobs._claim_is_live(job.get("run_claim"), now, _jobs._oneshot_run_claim_ttl_seconds()):
            raise ValueError("Cannot re-arm one-shot over a live run claim.")
        if _jobs._claim_is_live(job.get("fire_claim"), now, _jobs.FIRE_CLAIM_TTL_SECONDS):
            raise ValueError("Cannot re-arm one-shot over a live fire claim.")
        if job.get("schedule", {}).get("kind") != "once":
            raise ValueError(_REARM_RECURRING_ERROR)
        repeat = job.get("repeat") or {}
        repeat["completed"] = 0
        job.update(
            schedule=parsed_schedule, schedule_display=parsed_schedule.get("display", str(run_at)),
            repeat=repeat, run_claim=None, fire_claim=None)
        _jobs._activate_job_record(job)
        job["next_run_at"] = next_run_at
        _jobs.save_jobs(jobs)
        return _jobs._normalize_job_record(job)

    return _jobs._with_job(job_ref["id"], apply)


def remove_job(job_id: str) -> bool:
    """Remove a job by ID or name."""
    job = _jobs.resolve_job_ref(job_id)
    if not job:
        return False
    canonical_id = job["id"]
    with _jobs._jobs_lock():
        jobs = _jobs.load_jobs()
        original_len = len(jobs)
        jobs = [j for j in jobs if j["id"] != canonical_id]
        if len(jobs) == original_len:
            return False
        # Resolve BEFORE saving so a legacy unsafe ID fails closed without a half-applied removal.
        job_output_dir = _jobs._job_output_dir(canonical_id)
        _jobs.save_jobs(jobs, removed_ids={canonical_id})
        marker = _jobs._self_removal_delivery.get()
        if marker is not None and marker.job_id == canonical_id:
            marker.removed = True
        if job_output_dir.exists():
            shutil.rmtree(job_output_dir)
        try:
            from cron.notepad import clear_notepad
            clear_notepad(canonical_id)
        except Exception:
            logger.debug("Failed to clear notepad for removed job %s", canonical_id, exc_info=True)
        # Prune the fire-fence lock entry so the registry doesn't grow monotonically.
        _fence_key = f"{_jobs._current_cron_store().cron_dir.resolve()}::{canonical_id}"
        with _jobs._fire_fence_locks_guard:
            _jobs._fire_fence_locks.pop(_fence_key, None)
        return True


def _set_alert_flag(job_id: str, field: str, value: bool) -> bool:
    """Set/clear a persisted alert-dedup marker (alert exactly once until the condition heals;
    survives restarts) and return the PRIOR value. Field: ``preflight_alerted`` (blocked config).

    The marker records that the operator was already alerted about this job's condition, so the scheduler
    alerts exactly once and stays silent on subsequent ticks until the condition heals (same alert-once
    shape as the dead-pin auto-pause in #73506).
    """
    def apply(jobs, _i, job):
        prior = bool(job.get(field))
        if value:
            job[field] = True
        else:
            job.pop(field, None)
        if prior != value:
            _jobs.save_jobs(jobs)
        return prior

    return _jobs._with_job(job_id, apply, False)


def mark_preflight_alerted(job_id: str) -> bool:
    """Mark the job as preflight-alerted; return True if it already was."""
    return _set_alert_flag(job_id, "preflight_alerted", True)


def clear_preflight_alerted(job_id: str) -> None:
    """Clear the preflight alert-dedup marker (config validates again)."""
    _set_alert_flag(job_id, "preflight_alerted", False)


def note_fire_forward_failure(job_id: str, detail: str) -> bool:
    """Durably record (as ``last_fire_error``) that a scheduled fire could not be handed to the
    runner — written by the dashboard fire webhook when the loopback forward fails. Without it
    the miss is invisible (no execution row, last_status only covers started runs); mark_job_run
    clears it."""
    def apply(jobs, _i, job):
        job["last_fire_error"] = {
            "at": _jobs._hermes_now().isoformat(), "detail": str(detail or "")[:500]}
        _jobs.save_jobs(jobs)
        return True

    return _jobs._with_job(job_id, apply, False)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
