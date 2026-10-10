"""Running one cron job's agent: prompt preparation, the per-run scope, the inactivity watchdog,
the deliverable final response, session finalization, usage audit, ``run_job`` and teardown.

Split out of ``cron.scheduler``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_sched`` (import-cycle breaking), so
monkeypatching ``cron.scheduler.<name>`` keeps working.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import logging
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger("cron.scheduler")  # log-record parity with the origin module


def _raise_inactivity_timeout(agent, job_name: str, limit_s: float) -> None:
    """Log the agent's last activity, hard-interrupt it and raise TimeoutError."""
    _activity = {}
    if hasattr(agent, "get_activity_summary"):
        with contextlib.suppress(Exception):
            _activity = agent.get_activity_summary()
    _last_desc = _activity.get("last_activity_desc", "unknown")
    _secs_ago = _activity.get("seconds_since_activity", 0)
    logger.error(
        "Job '%s' idle for %.0fs (inactivity limit %.0fs) "
        "| last_activity=%s | iteration=%s/%s | tool=%s",
        job_name, _secs_ago, limit_s,
        _last_desc, _activity.get("api_call_count", 0), _activity.get("max_iterations", 0),
        _activity.get("current_tool") or "none")
    _sched.request_hard_interrupt(agent, "Cron job timed out (inactivity)")
    raise TimeoutError(
        f"Cron job '{job_name}' idle for "
        f"{int(_secs_ago)}s (limit {int(limit_s)}s) "
        f"— last activity: {_last_desc}")


def _run_agent_with_watchdog(
    agent, prompt: str, job: dict, job_id: str, job_name: str, task_id: str, cancel_event,
    worker_state: Optional[dict] = None,
) -> dict:
    """Run ``agent.run_conversation`` on a worker thread under the inactivity (not wall-clock)
    watchdog: default 600s, override HERMES_CRON_TIMEOUT, 0 = unlimited."""
    _cron_timeout = _sched._cron_inactivity_seconds()
    _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
    _POLL_INTERVAL = 5.0
    # Heartbeat the one-shot run_claim while alive: without it a long run looks like a dead owner
    # and gets re-dispatched / stale-removed out from under the live run.
    # Keep the one-shot run_claim fresh while the run is alive (#62002): the claim TTL is a dead-owner
    # detector, but without a heartbeat a run that legitimately outlives it (stream stall, laptop asleep
    # mid-run) is indistinguishable from a dead tick — another process re-dispatches it and get_due_jobs
    # stale-removes the job record out from under the live run. Refreshing the claim from this monitor keeps
    # "expired claim" meaning "owner died".
    _job_schedule = job.get("schedule")
    _is_oneshot = isinstance(_job_schedule, dict) and _job_schedule.get("kind") == "once"
    _run_claim = job.get("run_claim")
    _run_claim_owner = str(_run_claim.get("by") or "") if isinstance(_run_claim, dict) else ""
    _last_claim_heartbeat = time.monotonic()

    def _abort_if_fire_claim_lost() -> None:
        if cancel_event is None or not cancel_event.is_set():
            return
        if agent is not None and hasattr(agent, "interrupt"):
            agent.interrupt("Cron fire claim ownership was lost")
        raise RuntimeError(f"Cron job '{job_name}' lost its durable fire claim ownership")

    def _heartbeat_run_claim_if_due():
        nonlocal _last_claim_heartbeat
        if not _is_oneshot or not _run_claim_owner:
            return
        _mono = time.monotonic()
        if _mono - _last_claim_heartbeat < _sched._RUN_CLAIM_HEARTBEAT_SECONDS:
            return
        _last_claim_heartbeat = _mono
        try:
            _sched.heartbeat_run_claim(job_id, expected_owner=_run_claim_owner)
        except Exception:
            logger.debug("Job '%s': run_claim heartbeat failed", job_name, exc_info=True)

    _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    # Carry scheduler-scoped ContextVar state (e.g. env passthrough) into the worker thread.
    _cron_context = contextvars.copy_context()
    _cron_future = _cron_pool.submit(
        _cron_context.run, agent.run_conversation, prompt, task_id=task_id)
    if worker_state is not None:
        worker_state["future"] = _cron_future
    _inactivity_timeout = False
    _watch_stop = threading.Event()

    def _idle_seconds() -> float:
        if not hasattr(agent, "get_activity_summary"):
            return 0.0
        try:
            _act = agent.get_activity_summary()
            return float(_act.get("seconds_since_activity", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _watch_inactivity() -> None:
        nonlocal _inactivity_timeout
        if _cron_inactivity_limit is None:
            return
        if _sched._inactivity_watchdog_loop(
            get_idle_seconds=_idle_seconds, limit_s=_cron_inactivity_limit, poll_s=_POLL_INTERVAL,
            stop=_watch_stop, future_done=_cron_future.done):
            _inactivity_timeout = True

    _watch_thread = threading.Thread(
        target=_watch_inactivity, name=f"cron-inactivity-{str(job_id)[:8]}", daemon=True)
    try:
        if _cron_inactivity_limit is not None:
            # Separate daemon thread so a hung get_activity_summary can't stop the limit firing.
            # Daemon thread: kernel ``Event.wait`` timeout, independent of the ``run_job`` thread. A blocked
            # loop / hung ``get_activity_summary`` on this thread can no longer keep the 600s inactivity
            # limit from firing (#94285).
            _watch_thread.start()
        if _cron_inactivity_limit is None and not _is_oneshot and cancel_event is None:
            result = _cron_future.result()
        else:
            result = None
            while True:
                done, _ = concurrent.futures.wait({_cron_future}, timeout=_POLL_INTERVAL)
                if done:
                    _abort_if_fire_claim_lost()
                    result = _cron_future.result()
                    break
                if _inactivity_timeout:
                    break
                _abort_if_fire_claim_lost()
                _heartbeat_run_claim_if_due()
    except Exception:
        _cron_pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        _watch_stop.set()
        _cron_pool.shutdown(wait=False, cancel_futures=True)

    if _inactivity_timeout:
        _raise_inactivity_timeout(agent, job_name, _cron_inactivity_limit)

    if not isinstance(result, dict):
        raise RuntimeError(
            f"agent.run_conversation returned {type(result).__name__} instead of dict: {result!r}"
        )
    return result


def _final_response_from_result(result: dict, job_id: str, job_name: str, AIAgent) -> str:
    """Deliverable final response from a ``run_conversation`` result. Raises RuntimeError on
    `failed=True`/`completed=False`: the error text may sit in `final_response` and would otherwise
    be delivered as the reply with the job marked ok."""
    # If the agent itself reported failure (e.g. all retries exhausted on API errors, model abort, mid-run
    # interrupt), do not silently mark the job as successful. run_agent populates
    # `failed=True`/`completed=False` on these paths and may put the error into `final_response`, which
    # would otherwise be delivered as if it were the agent's reply and the job's `last_status` set to "ok".
    # Raise so the except handler below builds the proper failure tuple. (issue #17855)
    turn_exit_reason = str(result.get("turn_exit_reason") or "")
    final_response_text = (result.get("final_response") or "").strip()
    max_iteration_summary = (
        result.get("failed") is not True
        and result.get("completed") is False
        and turn_exit_reason.startswith("max_iterations_reached(")
        and bool(final_response_text)
    )
    if result.get("failed") is True or (result.get("completed") is False and not max_iteration_summary):
        failure = RuntimeError(result.get("error") or final_response_text or "agent reported failure")
        # The classifier's verdict travels with it: cron/unreachable_retry.py re-runs a fire the
        # provider was too busy (or slow) to answer before any model call completed.
        failure.failure_reason = str(result.get("failure_reason") or "")
        raise failure
    if max_iteration_summary:
        logger.warning(
            "Job '%s' reached the iteration limit but produced a final fallback response; "
            "delivering the response instead of failing the cron run",
            job_name)

    final_response = result.get("final_response", "") or ""
    # Repair model-mangled computer_use media paths before delivery (fail-open, as in gateway).
    if final_response:
        from gateway.media_repair import repair_explicit_computer_use_media_paths

        final_response = repair_explicit_computer_use_media_paths(
            final_response, result.get("messages", []))
    if final_response.strip() == "(No response generated)":
        final_response = ""
    # The "⚠️ No reply" turn-completion explainer would be delivered as a cron warning; detect it
    # via the same formatter and treat as empty so cron stays silent on abnormal empty turns.
    if final_response.strip() and turn_exit_reason:
        # Render every persistence-cause variant or cause-refined text slips through.
        _explainer_variants = []
        try:
            from hermes_state_errors import PERSISTENCE_ERROR_CAUSES as _causes
        except Exception:
            _causes = ("locked", "disk", "unknown")
        # The finalizer fills the model name into the explainer; render with the same name (and
        # the bare form) or the comparison below misses and the warning is delivered.
        _model = str(result.get("model") or "")
        for _cause in (None, *_causes):
            for _kwargs in ({"model": _model}, {}):
                try:
                    _variant = AIAgent._format_turn_completion_explanation(turn_exit_reason, _cause, **_kwargs)
                except TypeError:
                    try:
                        _variant = AIAgent._format_turn_completion_explanation(turn_exit_reason)
                    except Exception:
                        _variant = ""
                except Exception:
                    _variant = ""
                if _variant:
                    _explainer_variants.append(_variant.strip())
        if final_response.strip() in _explainer_variants:
            logger.info(
                "Job '%s': abnormal empty turn (%s) — suppressing explainer for cron delivery",
                job_id, turn_exit_reason)
            final_response = ""
    return final_response


def _finalize_cron_session(session_db, agent, job_id: str, job_name: str, cron_session_id: str) -> None:
    """Title, classify, end and release the cron session after the agent turn has returned."""
    # Bound every DB op so storage failure cannot hold the dispatch guard.
    _session_db = _sched._BoundedCronSessionDB(session_db, job_id)
    # Compression may have rotated the run onto a continuation: finalize that, not the stale cron
    # id. SessionDB lineage is authoritative; agent.session_id is only a fail-safe.
    _final_cron_session_id = cron_session_id
    try:
        _compression_tip = _session_db.get_compression_tip(cron_session_id)
        if _compression_tip:
            _final_cron_session_id = _compression_tip
    except (Exception, KeyboardInterrupt) as e:
        with contextlib.suppress((Exception, KeyboardInterrupt)):
            _agent_session_id = getattr(agent, "session_id", None)
            # CLI (single-process) path: the approval contextvar is only bound during gateway/TUI turns and
            # HERMES_SESSION_KEY is not in the CLI environment, so the key resolves empty here. Since #64240
            # the CLI drains completions through a positive-ownership filter keyed on the durable
            # AIAgent.session_id — an empty session_key would fail closed and the CLI could never claim its
            # own completions, while a restored foreign event with an empty key could leak into any
            # unfiltered consumer (#64484). Stamp the parent's durable session id instead; compression
            # rotations are handled on the drain side via resolve_resume_session_id lineage resolution.
            if _agent_session_id:
                _final_cron_session_id = _agent_session_id
        logger.debug("Job '%s': failed to resolve cron compression tip: %s", job_id, e)
    # Title must persist BEFORE end_session()/close(). Run-time suffix keeps it unique against the
    # sessions.title index; the fallbacks below guarantee a non-blank title.
    try:
        # Title the cron session from the job (name -> id) and PERSIST it BEFORE end_session()/close() tear
        # the connection down, so the close can never run over an in-flight title write (#50536).
        _title_base = " ".join(job_name.split())[:60].strip() or f"cron {job_id}"
        _cron_title = f"{_title_base} · {_sched._hermes_now().strftime('%b %d %H:%M')}"
        if not _sched._set_cron_session_title(_session_db, _final_cron_session_id, _cron_title):
            _sched._set_cron_session_title(_session_db, _final_cron_session_id, f"cron {job_id}")
    except (Exception, KeyboardInterrupt) as e:
        logger.debug("Job '%s': failed to set cron session title: %s", job_id, e)
        # Never leave the session untitled.
        # Try the next free title in the lineage, then a bare id-stamped title. See #50535.
        for _fallback in (
            getattr(_session_db, "get_next_title_in_lineage", lambda b: b)(f"cron {job_id}"),
            f"cron {job_id} {_final_cron_session_id[-6:]}"):
            try:
                if _sched._set_cron_session_title(_session_db, _final_cron_session_id, _fallback):
                    break
            except (Exception, KeyboardInterrupt):
                continue
    # Book cron_complete only when the last row is a real assistant reply ([SILENT] counts). Only a
    # POSITIVELY recognized bad status downgrades (keep tuple in sync with
    # session_lifecycle_statuses); unknown values / probe failures fail OPEN.
    # Verified completion booking (#93820): the run may only be recorded as cron_complete when the session's
    # LAST message row is a real assistant reply — a plain answer or the [SILENT] sentinel (both are
    # assistant-text rows, so both classify as 'complete'). A turn that died after a tool call,
    # mid-API-wait, or without any assistant text leaves the last row as a tool result / pending call / user
    # prompt and must not surface as a healthy run. session_lifecycle_statuses is the existing cost-bounded
    # classifier for exactly this shape. Only a POSITIVELY recognized pathological status (see the status
    # vocabulary in hermes_state's session_lifecycle_statuses docstring — keep the tuple below in sync when
    # it grows) downgrades the booking: an unknown value (newer classifier shape, test doubles) keeps the
    # historical reason, and so does a failed probe — the booking itself is FAIL-OPEN on probe errors,
    # because classification is best-effort metadata and must not mislabel a healthy run.
    _end_reason = "cron_complete"
    try:
        _statuses = _session_db.session_lifecycle_statuses([_final_cron_session_id])
        _lifecycle = _statuses.get(_final_cron_session_id)
        if _lifecycle in ("interrupted", "error", "empty"):
            _end_reason = "cron_incomplete_no_output"
            logger.warning(
                "Job '%s': session ended without a final assistant "
                "message (lifecycle=%s) — booking run as %s",
                job_id, _lifecycle, _end_reason)
    except (Exception, KeyboardInterrupt) as e:
        logger.debug("Job '%s': session lifecycle classification failed: %s", job_id, e)
    try:
        _session_db.end_session(_final_cron_session_id, _end_reason)
        # The scheduler owns cron-session finalization. AIAgent.close() also
        # finalizes owned session rows by default; once the shared SessionDB is
        # released below, that second end_session() would reopen the just-closed
        # SQLite handle (#94736). The reason is durably booked, so disarm only the
        # agent's redundant row-finalization; its resource teardown still runs in
        # _teardown_cron_agent.
        if agent is not None:
            agent._end_session_on_close = False
    except (Exception, KeyboardInterrupt) as e:
        logger.debug("Job '%s': failed to end session: %s", job_id, e)
    try:
        from hermes_state_registry import release_or_close
        release_or_close(_session_db)
    except (Exception, KeyboardInterrupt) as e:
        logger.debug("Job '%s': failed to close SQLite session store: %s", job_id, e)


def _run_doc_header(job: dict, title: str, job_id: str, prompt: str) -> str:
    """Header of the persisted run document (title, ids, schedule, prompt)."""
    return (
        f"# Cron Job: {title}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {_sched._hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Schedule:** {job.get('schedule_display', 'N/A')}\n\n"
        f"## Prompt\n\n{prompt}\n\n"
    )


_RunResult = tuple[bool, str, str, Optional[str]]


def _prepare_job_prompt(
    job: dict, job_id: str, job_name: str, extra_prompt: Optional[str], cancel_event,
) -> tuple[Optional[_RunResult], Optional[str]]:
    """Run every pre-agent gate and build the prompt. Returns ``(early_result, prompt)``: an early
    result short-circuits ``run_job`` (no_agent job, empty payload, monitor gate, wake gate,
    injection block, empty prompt); otherwise ``prompt`` is set."""
    # Fail closed on a corrupt config.yaml: defaults would let auto-detection bill a provider the
    # user never chose. no_agent jobs are exempt. Escape hatch: HERMES_IGNORE_USER_CONFIG=1.
    if not job.get("no_agent"):
        from hermes_cli.config import InvalidUserConfigError, require_parseable_user_config

        try:
            require_parseable_user_config()
        except InvalidUserConfigError as exc:
            logger.error("Job '%s': refusing to run — %s", job_id, exc)
            return (False, f"# Cron Job: {job_name}\n\nError: {exc}\n", "", str(exc)), None

    # no_agent short-circuits BEFORE importing run_agent / opening SessionDB.
    if job.get("no_agent"):
        return _sched._run_no_agent_job(job, job_id, job_name, cancel_event), None

    # Legacy / hand-edited job with nothing to run: pause it instead of waking the LLM every fire.
    from cron.jobs import EMPTY_PAYLOAD_ERROR, job_payload_is_empty

    if job_payload_is_empty(job):
        return _sched._block_and_pause_job(job_id, job_name, EMPTY_PAYLOAD_ERROR), None

    _early, extra_prompt = _sched._apply_monitor_gate(job, job_id, job_name, extra_prompt)
    if _early is not None:
        return _early, None

    # Wake-gate: run the pre-check script BEFORE building the prompt; its result is passed into
    # _build_job_prompt so the script runs only once.
    # NOTE: the SQLite session store used to be initialized here, BEFORE the wake-gate and prompt-validation
    # early returns below. Every gated run (``wakeAgent: false``, blocked prompt) opened state.db and
    # returned without reaching the finally that closes it, relying on GC to release the handle. Init now
    # happens inside the main try, right before the agent is constructed — after every early-return path
    # (#96290).
    prerun_script = None
    script_path = job.get("script")
    if script_path:
        prerun_script = _sched._run_job_script_with_claim_heartbeat(
            job,
            script_path,
            workdir=_sched._resolve_job_workdir(job, job_id),
            cancel_event=cancel_event,
        )
        _ran_ok, _script_output = prerun_script
        if _ran_ok and not _sched._parse_wake_gate(_script_output):
            logger.info("Job '%s' (ID: %s): wakeAgent=false, skipping agent run", job_name, job_id)
            silent_doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {_sched._hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "Script gate returned `wakeAgent=false` — agent skipped.\n"
            )
            return (True, silent_doc, _sched.SILENT_MARKER, None), None

    try:
        prompt = _sched._build_job_prompt(job, prerun_script=prerun_script, extra_prompt=extra_prompt)
    except _sched.CronPromptInjectionBlocked as block_exc:
        # Injection scanner tripped: refuse this tick and tell the operator WHY.
        logger.warning(
            "Job '%s' (ID: %s): blocked by prompt-injection scanner — %s", job_name, job_id, block_exc,
        )
        blocked_doc = (
            f"# Cron Job: {job_name}\n\n"
            f"**Job ID:** {job_id}\n"
            f"**Run Time:** {_sched._hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Status:** BLOCKED\n\n"
            "The assembled prompt (user prompt + loaded skill content) tripped "
            "the cron injection scanner and the agent was NOT run.\n\n"
            f"**Scanner result:** {block_exc}\n\n"
            "Audit the skill(s) attached to this job for prompt-injection "
            "payloads or invisible-unicode markers. If the skill is legitimate "
            "and the match is a false positive, rephrase the content to avoid "
            "the threat pattern (`tools/cronjob_tools.py::_CRON_THREAT_PATTERNS`)."
        )
        return (False, blocked_doc, "", str(block_exc)), None
    if prompt is None:
        logger.info("Job '%s': script produced no output, skipping AI call.", job_name)
        return (True, "", _sched.SILENT_MARKER, None), None
    return None, prompt


_CRON_DELIVERY_VARS = (
    "HERMES_CRON_AUTO_DELIVER_PLATFORM",
    "HERMES_CRON_AUTO_DELIVER_CHAT_ID",
    "HERMES_CRON_AUTO_DELIVER_THREAD_ID")


class _CronRunScope:
    """Per-run ContextVar / tool-cwd scope for ``run_job`` (ContextVars, not os.environ, so
    parallel jobs don't clobber each other). Construct before the try, ``enter()`` as its first
    statement, ``exit()`` in the finally — every setter here has a matching reset there.

    HERMES_SESSION_* are deliberately NOT seeded from job["origin"]: it is delivery metadata, not
    a sender, and terminal/tts/skills/send_message tools would act as if the origin user were
    driving the agent. Delivery reads job["origin"] / HERMES_CRON_AUTO_DELIVER_* directly.
    """

    def __init__(self, job: dict, job_id: str, execution_id: Optional[str]):
        from gateway.session_context import set_session_vars, _VAR_MAP
        from tools.terminal_tool import record_session_cwd

        self._var_map = _VAR_MAP
        # Resolve workdir BEFORE set_session_vars so it owns the _SESSION_CWD set/clear.
        self.workdir = _sched._resolve_job_workdir(job, job_id)
        self._ctx_tokens = set_session_vars(
            platform="",
            chat_id="",
            chat_name="",
            # Cron can't receive completions after its turn; async delegation output could
            # otherwise route to an unrelated chat via the ambient session key => inline delegation.
            # We clear the HERMES_SESSION_* routing keys just below, so an async delegation's completion
            # event carries session_key="" — _enrich_async_delegation_routing cannot resolve it and
            # _inject_watch_notification drops it ("no routing metadata"). And by the time a child finishes,
            # run_job has already shipped the job's final response via _deliver_result; there is no turn
            # left to re-enter. (Worse, get_current_session_key() can fall back to the ambient os.environ
            # HERMES_SESSION_KEY, which risks routing a cron subagent's output into an unrelated user chat.)
            # Declaring the channel stateless routes delegate_task to its existing inline/synchronous path,
            # so results return within the job's own turn. See declare_stateless_channel(). Upstream:
            # #53027, #63142.
            async_delivery=False,
            cwd=self.workdir or "",
        )
        for name in _CRON_DELIVERY_VARS:
            _VAR_MAP[name].set("")
        # Workdir binds to the per-run task id (tool-layer cwd authority) instead of mutating
        # global TERMINAL_CWD; _SESSION_CWD above remains the prompt/context-file authority.
        self.task_id = f"cron:{job_id}:{execution_id or job.get('execution_id') or uuid.uuid4().hex}"
        if self.workdir:
            record_session_cwd(self.task_id, self.workdir)
        self._cron_session_var = _VAR_MAP["HERMES_CRON_SESSION"]
        self._cron_session_token = None
        self._approval_mode = job.get("approval_mode")
        self._approval_mode_token = None

    def enter(self) -> None:
        # Scope cron approval policy; exit() RESETS via token (pinning "" would suppress the legacy
        # os.environ fallback used by standalone entrypoints/tests).
        self._cron_session_token = self._cron_session_var.set("1")
        # A durable job grant applies only to this run context. copy_context() carries it
        # into the agent worker and delegated children without touching process globals.
        from tools.approval_context import set_cron_approval_mode_override
        self._approval_mode_token = set_cron_approval_mode_override(self._approval_mode)

    def exit(self) -> None:
        from gateway.session_context import clear_session_vars
        from tools.terminal_tool import clear_session_cwd

        clear_session_cwd(self.task_id)
        clear_session_vars(self._ctx_tokens)  # also clears _SESSION_CWD
        if self._cron_session_token is not None:
            self._cron_session_var.reset(self._cron_session_token)
        if self._approval_mode_token is not None:
            from tools.approval_context import reset_cron_approval_mode_override
            reset_cron_approval_mode_override(self._approval_mode_token)
        for name in _CRON_DELIVERY_VARS:
            self._var_map[name].set("")


def _reload_dotenv_and_publish_delivery_target(job: dict) -> None:
    """Re-read .env for this run and publish the auto-deliver target into the session ContextVars."""
    # Reset the secret-source cache FIRST or a Bitwarden/BSM-backed secret is never re-resolved
    # (only the placeholder reloads -> 401s).
    from hermes_cli.env_loader import load_hermes_dotenv, reset_secret_source_cache
    from gateway.session_context import _VAR_MAP

    reset_secret_source_cache(_sched._get_hermes_home())
    load_hermes_dotenv(hermes_home=_sched._get_hermes_home())

    delivery_target = _sched._resolve_delivery_target(job)
    if delivery_target:
        _VAR_MAP["HERMES_CRON_AUTO_DELIVER_PLATFORM"].set(delivery_target["platform"])
        _VAR_MAP["HERMES_CRON_AUTO_DELIVER_CHAT_ID"].set(str(delivery_target["chat_id"]))
        _VAR_MAP["HERMES_CRON_AUTO_DELIVER_THREAD_ID"].set(
            "" if delivery_target.get("thread_id") is None else str(delivery_target["thread_id"])
        )


class _FireAudit:
    """One usage_audit.jsonl line per fire (created once the agent exists; fire id + start clock)."""

    def __init__(self, job: dict, job_id: str, model: str):
        self.job, self.job_id, self.model = job, job_id, model
        self.fire_id = uuid.uuid4().hex
        self.t_start = time.monotonic()

    def write(self, result: dict, error: Optional[str]) -> None:
        _sched._write_usage_audit({
            "ts": _sched._utcnow_iso_ms(),
            "job_id": self.job_id,
            "fire_id": self.fire_id,
            "prompt_tokens": result.get("prompt_tokens"),
            "completion_tokens": result.get("completion_tokens"),
            "total_tokens": result.get("total_tokens"),
            "response_silent": bool(result.get("response_silent")),
            "deliver_target": self.job.get("deliver"),
            "model": self.model or None,
            "duration_ms": int((time.monotonic() - self.t_start) * 1000),
            "error": error})



def run_job(
    job: dict, *, defer_agent_teardown: Optional[list] = None, extra_prompt: Optional[str] = None,
    cancel_event: Optional[_sched._CancelEventLike] = None, execution_id: Optional[str] = None,
) -> tuple[bool, str, str, Optional[str]]:
    """Execute a single cron job. Returns (success, full_output_doc, final_response, error).
    ``defer_agent_teardown``: if a list, the live agent is appended instead of torn down; the caller
    MUST call ``_teardown_cron_agent(agent)`` AFTER delivery (a torn-down async client can't
    deliver). ``extra_prompt``: per-fire context, never persisted.

    ``defer_agent_teardown``: when a caller passes a list, ``run_job`` skips the agent's async-resource
    teardown (``agent.close()`` + ``cleanup_stale_async_clients()``) in its ``finally`` block and instead
    appends the live agent to that list. The caller is then responsible for calling
    ``_teardown_cron_agent(agent)`` AFTER it has delivered the result. This closes the ordering window in
    #58720 where delivery ran against a torn-down async client (defense-in-depth alongside the
    interpreter-shutdown guard). When ``None`` (the default) teardown happens inline as before, so every
    existing caller is unchanged.
    ``extra_prompt``: optional per-run context from ``cronjob(action='run', prompt=...)`` (#57331). Appended
    to the stored prompt for this fire only — never persisted to the job definition.
    """
    job_id = job["id"]
    job_name = str(job.get("name") or job.get("prompt") or job_id or "cron job")

    early, prompt = _prepare_job_prompt(job, job_id, job_name, extra_prompt, cancel_event)
    if early is not None:
        return early
    from run_agent import AIAgent

    _cron_session_id = f"cron_{job_id}_{_sched._hermes_now().strftime('%Y%m%d_%H%M%S')}"
    logger.info("Running job '%s' (ID: %s)", job_name, job_id)
    logger.info("Prompt: %s", prompt[:100])

    agent = None
    model = ""
    _session_db = None
    _audit: Optional[_FireAudit] = None
    _worker_state: dict = {}
    scope = _CronRunScope(job, job_id, execution_id)
    try:
        scope.enter()
        if scope.workdir:
            logger.info("Job '%s': using task-scoped workdir %s", job_id, scope.workdir)
        _reload_dotenv_and_publish_delivery_target(job)

        jc = _sched._load_cron_job_config(job, job_id, job_name)
        _cfg = jc.cfg
        model = jc.model
        setup = _sched._resolve_cron_agent_setup(job, job_id, job_name, jc)
        if setup.blocked is not None:
            return setup.blocked
        model = setup.model

        # Open state.db only after every early-return gate has passed.
        _session_db = _sched._open_cron_session_db(job)
        agent = _sched._construct_cron_agent(
            AIAgent, job, _cfg, setup, workdir=scope.workdir, session_id=_cron_session_id,
            session_db=_session_db)
        _audit = _FireAudit(job, job_id, model)

        result = _run_agent_with_watchdog(
            agent, prompt, job, job_id, job_name, scope.task_id, cancel_event,
            worker_state=_worker_state)
        final_response = _final_response_from_result(result, job_id, job_name, AIAgent)
        # Keep final_response clean for delivery logic (empty = no delivery).
        logged_response = final_response if final_response else "(No response generated)"
        output = _run_doc_header(job, job_name, job_id, prompt) + f"## Response\n\n{logged_response}\n"
        logger.info("Job '%s' completed successfully", job_name)
        _audit.write(dict(result, response_silent=_sched._is_cron_silence_response(final_response or "")), None)
        return True, output, final_response, None

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("Job '%s' failed: %s", job_name, error_msg)
        # Cowork-style unreachable-model re-run (cron/unreachable_retry.py): flag failures where
        # no model call completed (transient network/DNS, or the provider busy / rate-limited /
        # timing out) so the bookkeeping tail can schedule a bounded automatic re-run instead of
        # waiting a full period.
        try:
            from cron.unreachable_retry import is_model_unreachable_failure
            if is_model_unreachable_failure(e, agent):
                job["_model_unreachable"] = True
        except Exception:  # classification must never mask the real failure
            logger.debug("Job '%s': unreachable-failure classification failed", job_id)
        # No audit row when we failed before the agent existed; the audit write must never raise.
        if _audit is not None:
            _audit.write({}, error_msg)
        from cron.scheduler_diagnostics import format_run_error
        output = (
            _run_doc_header(job, f"{job_name} (FAILED)", job_id, prompt)
            + format_run_error(e)
        )
        return False, output, "", error_msg

    finally:
        from cron.scheduler_detached_worker import defer_teardown_to_running_worker
        _worker_teardown_deferred = defer_teardown_to_running_worker(
            _worker_state.get("future"), _session_db, agent, job_id, job_name, _cron_session_id)
        scope.exit()
        if _session_db and not _worker_teardown_deferred:
            _sched._finalize_cron_session(_session_db, agent, job_id, job_name, _cron_session_id)
        # Tear down the ephemeral agent or the gateway leaks fds per tick (EMFILE). With deferred
        # teardown, hand the live agent back: delivery needs a live async client.
        # Release subprocesses, terminal sandboxes, browser daemons, and the main OpenAI/httpx client held
        # by this ephemeral cron agent. Without this, a gateway that ticks cron every N minutes leaks fds
        # per job until it hits EMFILE (#10200 / "too many open files"). When the caller opted to defer
        # teardown (passed a list), hand the live agent back instead of closing it here — delivery must run
        # against a live async client, and the caller tears down afterwards (#58720).
        if not _worker_teardown_deferred:
            if defer_agent_teardown is not None:
                if agent is not None:
                    defer_agent_teardown.append(agent)
            else:
                _sched._teardown_cron_agent(agent, job_id)


def _teardown_cron_agent(
    agent, job_id: str, *, timeout_seconds: Optional[float] = None
) -> None:
    """Release an ephemeral cron agent's async resources within a hard bound (this runs outside the
    inactivity watchdog). Shared by ``run_job``'s finally and deferred post-delivery teardown.

    Split out of ``run_job``'s ``finally`` so a caller that defers teardown (to deliver first — #58720) can
    invoke the identical cleanup AFTER delivery. The timeout matters because this executes after
    ``run_conversation`` has returned, outside the agent inactivity watchdog.
    """
    def _cleanup_agent() -> None:
        try:
            if agent is not None:
                agent.close()
        except (Exception, KeyboardInterrupt) as e:
            logger.debug("Job '%s': failed to close agent resources: %s", job_id, e)
        # Worker-thread event loop dies with the executor; reap httpx clients cached under it.
        try:
            from agent.auxiliary_client import cleanup_stale_async_clients
            cleanup_stale_async_clients()
        except Exception as e:
            logger.debug("Job '%s': failed to reap stale auxiliary clients: %s", job_id, e)

    _sched._run_cron_cleanup_with_timeout(
        _cleanup_agent, job_id=job_id, label="agent resource teardown",
        timeout_seconds=timeout_seconds)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.scheduler`` re-exports from it.
from cron import scheduler as _sched  # noqa: E402
