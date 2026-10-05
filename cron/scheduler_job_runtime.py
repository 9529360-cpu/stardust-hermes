"""What an agent-backed cron run needs before the agent starts: toolsets, reasoning, config and
model pins, preflight, runtime + fallback chain, credential pool, MCP tools, the session store
and the ``AIAgent`` itself.

Split out of ``cron.scheduler``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_sched`` (import-cycle breaking), so
monkeypatching ``cron.scheduler.<name>`` keeps working.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("cron.scheduler")  # log-record parity with the origin module


def _resolve_cron_disabled_toolsets(cfg: dict, job: Optional[dict] = None) -> list[str]:
    """Toolsets a cron-spawned agent must never receive: ``messaging``/``clarify`` always
    (interactive); ``cronjob`` by default (loop prevention, not a security boundary).
    Global ``cron.allow_agent_scheduling: true`` lifts that loop-prevention gate, and so does
    a durable per-job ``approval_mode=approve`` grant: once the operator has explicitly
    delegated autonomous authority to a task, it may schedule/update follow-up work without
    a second unrelated config switch. ``agent.disabled_toolsets`` remains the user-owned hard
    ceiling, so an explicit ``cronjob`` deny there still wins.

    See #25752.
    """
    cron_cfg = (cfg or {}).get("cron") or {}
    delegated_scheduling = (job or {}).get("approval_mode") == "approve"
    if cron_cfg.get("allow_agent_scheduling") or delegated_scheduling:
        disabled = ["messaging", "clarify"]
    else:
        disabled = ["cronjob", "messaging", "clarify"]
    agent_cfg = (cfg or {}).get("agent") or {}
    from agent.skill_utils import parse_config_string_list

    user_disabled = parse_config_string_list(agent_cfg.get("disabled_toolsets"))
    for name in user_disabled:
        name = str(name).strip()
        if name and name not in disabled:
            disabled.append(name)
    return disabled

def _merge_mcp_into_per_job_toolsets(per_job: list[str], cfg: dict) -> list[str]:
    """Layer enabled MCP servers onto a per-job ``enabled_toolsets`` allowlist (else a per-job list
    silently drops every MCP server). Mirrors ``_get_platform_tools``: ``no_mcp`` sentinel -> none
    (stripped); any MCP server already listed -> allowlist, add nothing; else union all enabled."""
    result = [t for t in per_job if t != "no_mcp"]
    if "no_mcp" in per_job:
        return result
    # lazy: avoid heavy hermes_cli import at module load; shares MCP-membership with gateway/CLI
    from hermes_cli.tools_config import enabled_mcp_server_names
    enabled_mcp = enabled_mcp_server_names(cfg)
    if set(result) & enabled_mcp:
        return result
    for name in sorted(enabled_mcp):
        if name not in result:
            result.append(name)
    return result


def _resolve_cron_enabled_toolsets(job: dict, cfg: dict) -> list[str] | None:
    """Toolset list for a cron job. Precedence: per-job ``enabled_toolsets`` (+ MCP merge) >
    ``cron`` platform config (``_get_platform_tools``, which strips _DEFAULT_OFF_TOOLSETS so fresh
    installs run without ``moa``) > ``None`` on any failure (full default set).

    1. Per-job ``enabled_toolsets`` (set via ``cronjob`` tool on create/update). Keeps the agent's
    job-scoped toolset override intact — #6130. Enabled MCP servers are layered on per
    ``_merge_mcp_into_per_job_toolsets`` so a native-toolset allowlist does not silently strip MCP tools. 2.
    Mirrors gateway behavior (``_get_platform_tools(cfg, platform_key)``) so users can gate cron toolsets
    globally without recreating every job. 3. ``None`` on any lookup failure — AIAgent loads the full
    default set (legacy behavior before this change, preserved as the safety net).
    """
    per_job = job.get("enabled_toolsets")
    if per_job:
        return _merge_mcp_into_per_job_toolsets(list(per_job), cfg or {})
    try:
        from hermes_cli.tools_config import _get_platform_tools  # lazy: avoid heavy import at cron module load
        return sorted(_get_platform_tools(cfg or {}, "cron"))
    except Exception as exc:
        logger.warning(
            "Cron toolset resolution failed, falling back to full default toolset: %s",
            exc)
        return None


def _resolve_job_reasoning_config(job: dict, cfg: dict, model: str) -> dict | None:
    """Effective reasoning config for a cron run. A per-job ``reasoning_effort`` pin beats global
    and per-model config and is model-independent by design (also governs an auth-fallback swap);
    clamping stays with provider transports. An unparseable pin warns and falls back to config."""
    from hermes_constants import parse_reasoning_effort, resolve_reasoning_config

    pinned = job.get("reasoning_effort")
    if pinned is not None:
        parsed = parse_reasoning_effort(pinned)
        if parsed is not None:
            logger.info("Job '%s': using per-job reasoning_effort '%s'", job.get("id", "?"), pinned)
            return parsed
        logger.warning(
            "Job '%s': invalid stored reasoning_effort %r — ignoring the pin "
            "and falling back to config resolution. Fix with `cronjob "
            "action=update job_id=%s reasoning_effort=<level>` (valid: none, "
            "minimal, low, medium, high, xhigh, max, ultra).",
            job.get("id", "?"),
            pinned,
            job.get("id", "?"))
    return resolve_reasoning_config(cfg if isinstance(cfg, dict) else {}, str(model))


@dataclass
class _CronJobConfig:
    """Config-derived inputs for one agent-backed cron run."""

    cfg: dict
    model: str
    model_cfg: Any
    cron_default_provider: str


def _snapshot_pin(job: dict, axis: str, current: str, job_id: str) -> str:
    """The creation snapshot is an unpinned axis's effective pin: return it, logging once when it
    differs from *current* (the live global default); ``""`` for legacy jobs without one, which keep
    following the global default. A global model/provider change must never stop a cron job; a job
    keeps running on what it was created under until the operator pins it or sets a cron.* fleet
    default (#44585)."""
    snapshot = str(job.get(f"{axis}_snapshot") or "").strip()
    if snapshot and current and snapshot.lower() != current.lower():
        logger.info(
            "Job '%s': running on creation-snapshot %s %r (global default is now %r); "
            "`hermes cron resnap %s` adopts the new default (stays unpinned), "
            "`hermes cron edit %s --%s <value>` or cron.%s in config.yaml pins it.",
            job_id, axis, snapshot, current, job_id, job_id, axis,
            "model" if axis == "model" else "model_provider")
    return snapshot


def _load_cron_job_config(job: dict, job_id: str, job_name: str) -> _CronJobConfig:
    """Load config.yaml and resolve the run's model: per-job override > cron.model (fleet default) >
    creation snapshot > HERMES_MODEL > config ``model:``. Re-read every tick (no cache) so
    ``hermes cron edit --model`` applies next tick."""
    model = job.get("model") or _sched.cron_env_setting("HERMES_MODEL") or ""
    _cron_default_provider = ""
    _cfg: dict = {}
    _model_cfg: Any = {}
    try:
        from hermes_cli.config_effective import load_user_config_effective
        _cfg_path = str(_sched._get_hermes_home() / "config.yaml")
        if os.path.exists(_cfg_path):
            _cfg = load_user_config_effective(_sched.Path(_cfg_path))
            # Coerce null to {} so a falsy default never clobbers a resolved env value.
            _model_cfg = _cfg.get("model") or {}
            _cron_cfg_for_model = _cfg.get("cron") or {}
            _cron_default_model = ""
            if isinstance(_cron_cfg_for_model, dict):
                _cron_default_model = str(_cron_cfg_for_model.get("model") or "").strip()
                _cron_default_provider = str(_cron_cfg_for_model.get("model_provider") or "").strip()
            if not job.get("model"):
                if _cron_default_model:
                    model = _cron_default_model
                else:
                    _, _global_model = _sched.resolve_cron_model_drift_defaults(
                        _cfg, environ={"HERMES_MODEL": _sched.cron_env_setting("HERMES_MODEL")})
                    model = _snapshot_pin(job, "model", _global_model, job_id) or _global_model or model
    except Exception as e:
        logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)

    # Fail fast: an empty model otherwise reaches the provider as an opaque 400.
    # See #23979.
    if not (isinstance(model, str) and model.strip()):
        raise RuntimeError(
            f"Cron job '{job_name}' has no model configured "
            f"(job.model={job.get('model')!r}, "
            f"HERMES_MODEL={_sched.cron_env_setting('HERMES_MODEL')!r}, "
            "config.yaml model.default missing or empty). "
            f"Set a per-job model via "
            f"`hermes cron edit {job_id} --model <name>` or set a "
            "default with `hermes model <name>`."
        )

    with contextlib.suppress(Exception):
        from hermes_constants import apply_ipv4_preference
        _net_cfg = _cfg.get("network", {})
        if isinstance(_net_cfg, dict) and _net_cfg.get("force_ipv4"):
            apply_ipv4_preference(force=True)
    return _CronJobConfig(_cfg, model, _model_cfg, _cron_default_provider)


def _load_prefill_messages(cfg: dict, job_id: str) -> Optional[list]:
    """Prefill messages from env or config.yaml (top-level key canonical; agent.* is legacy)."""
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    prefill_file = (
        _sched.cron_env_setting("HERMES_PREFILL_MESSAGES_FILE")
        or cfg.get("prefill_messages_file", "")
        or agent_cfg.get("prefill_messages_file", "")
    )
    if not prefill_file:
        return None
    pfpath = _sched.Path(prefill_file).expanduser()
    if not pfpath.is_absolute():
        pfpath = _sched._get_hermes_home() / pfpath
    if not pfpath.exists():
        return None
    try:
        with open(pfpath, "r", encoding="utf-8") as _pf:
            prefill_messages = json.load(_pf)
        return prefill_messages if isinstance(prefill_messages, list) else None
    except Exception as e:
        logger.warning("Job '%s': failed to parse prefill messages file '%s': %s", job_id, pfpath, e)
        return None


def _preflight_or_block(job: dict, job_id: str, job_name: str, cfg: dict) -> Optional[tuple]:
    """Pre-dispatch config validation: refuse unrunnable jobs (missing key, unready skill,
    unconfigured delivery) BEFORE AIAgent is built. run_one_job keys off BLOCKED_CONFIG_MARKER to
    record blocked_config and alert once (`preflight_alerted` bit). Must run after the wake gate so
    silent ticks stay silent. Opt-out: `cron.preflight: false`. Returns failure tuple or None.
    """
    # --------------------------------------------------------------- Pre-dispatch configuration validation
    # (T1-26). A job whose configuration cannot possibly produce a successful run — missing provider API key
    # (no fallback chain), unready attached skill, unconfigured delivery platform — is refused HERE, before
    # AIAgent is constructed and before the resolution below can feed a doomed runtime into it, so a
    # misconfigured job never burns an LLM call. run_one_job keys off the BLOCKED_CONFIG_MARKER in the
    # returned error to record last_status='blocked_config' and alert exactly once (dedup persisted via the
    # job's `preflight_alerted` bit — the #73506 alert-once shape).
    _pf_reason = None
    try:
        if _sched._cron_preflight_enabled(cfg):
            _pf_reason = _sched._preflight_job_config(job, cfg)
            if not _pf_reason and job.get("preflight_alerted"):
                # Config healthy again: clear alert-once marker so a future break re-alerts.
                with contextlib.suppress(Exception):
                    from cron.jobs import clear_preflight_alerted
                    clear_preflight_alerted(job_id)
    except Exception:
        # Fail open: the validator must never take down a runnable job.
        logger.debug("Job '%s': preflight validation errored — failing open", job_id, exc_info=True)
        _pf_reason = None
    if not _pf_reason:
        return None
    return _blocked_config_result(job_id, job_name, _pf_reason)


def _blocked_config_result(job_id: str, job_name: str, _pf_reason: str) -> tuple:
    """The ``blocked_config`` failure tuple for *_pf_reason*, alerting once per job."""
    logger.warning(
        "Job '%s' (ID: %s): BLOCKED by pre-dispatch config validation — %s (no LLM call was made)",
        job_name, job_id, _pf_reason)
    already_alerted = False
    try:
        from cron.jobs import mark_preflight_alerted
        already_alerted = mark_preflight_alerted(job_id)
    except Exception:
        logger.debug("Job '%s': could not persist preflight alert marker", job_id, exc_info=True)
    marker = _sched.BLOCKED_CONFIG_SILENT_MARKER if already_alerted else _sched.BLOCKED_CONFIG_MARKER
    blocked_doc = (
        f"# Cron Job: {job_name}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {_sched._hermes_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Status:** BLOCKED (configuration)\n\n"
        "The pre-run configuration check found a problem, so the agent did not run "
        "(nothing was charged).\n\n"
        f"**Reason:** {_pf_reason}\n\n"
        "Hermes tries again at the next scheduled time and clears this state on the first healthy "
        "run; this alert is not repeated. Check with `hermes cron doctor`. Set `cron.preflight: "
        "false` in config.yaml to disable this check."
    )
    return False, blocked_doc, "", f"{marker} {_pf_reason}"


def _resolve_job_runtime(job: dict, job_id: str, jc: _CronJobConfig) -> tuple[dict, str]:
    """Resolve the runtime, walking the fallback chain on auth/transient-network errors. Returns
    ``(runtime, model)``; provider+model swap atomically (never swap only the provider while keeping
    a paid primary model). Provider precedence: per-job pin > cron.model_provider > creation
    snapshot > persisted global config."""
    from hermes_cli.runtime_provider import (
        resolve_runtime_provider, format_runtime_provider_error)
    from hermes_cli.auth import AuthError

    model = jc.model
    requested = job.get("provider") or jc.cron_default_provider or None
    if not requested:
        global_provider = (
            str(jc.model_cfg.get("provider") or "").strip() if isinstance(jc.model_cfg, dict) else "")
        # None (not the config provider) keeps the legacy no-snapshot path resolving from persisted
        # config exactly as before.
        requested = _snapshot_pin(job, "provider", global_provider, job_id) or None
    try:
        # Do NOT pass HERMES_INFERENCE_PROVIDER as `requested`: it would override persisted config
        # and resurrect stale providers for unpinned jobs.
        runtime_kwargs = {
            "requested": requested,
            # api_mode must derive from the model actually run, not the stale persisted default.
            "target_model": model,
        }
        if job.get("base_url"):
            runtime_kwargs["explicit_base_url"] = job.get("base_url")
        return resolve_runtime_provider(**runtime_kwargs), model
    except Exception as resolve_exc:
        # Walk the fallback chain on AuthError AND transient network/DNS failures (e.g. during
        # OAuth refresh); anything else re-raises.
        is_auth = isinstance(resolve_exc, AuthError)
        is_transient_net = _sched._is_transient_provider_resolve_error(resolve_exc)
        if not (is_auth or is_transient_net):
            raise RuntimeError(format_runtime_provider_error(resolve_exc)) from resolve_exc

        logger.warning(
            "Job '%s': primary provider resolve failed (%s: %s), trying fallback",
            job_id, "auth" if is_auth else "transient network", resolve_exc)
        for entry in _sched.get_fallback_chain(jc.cfg):
            if not isinstance(entry, dict):
                continue
            fb_provider = str(entry.get("provider") or "").strip()
            fb_model = str(entry.get("model") or "").strip()
            if not fb_provider or not fb_model:
                continue
            try:
                from hermes_cli.fallback_config import effective_runtime_provider, resolve_entry_api_key

                fb_kwargs = {"requested": fb_provider, "target_model": fb_model}
                if entry.get("base_url"):
                    fb_kwargs["explicit_base_url"] = entry["base_url"]
                fb_api_key = resolve_entry_api_key(entry)
                if fb_api_key:
                    fb_kwargs["explicit_api_key"] = fb_api_key
                runtime = resolve_runtime_provider(**fb_kwargs)
                # Named custom entries resolve to the bare "custom" billing class; keep the configured
                # identity so job sessions record the provider name (#98739).
                runtime["provider"] = effective_runtime_provider(entry, runtime)
                logger.info(
                    "Job '%s': fallback resolved to %s model %s",
                    job_id, runtime.get("provider"), fb_model)
                return runtime, fb_model
            except Exception as fb_exc:
                logger.debug("Job '%s': fallback %s failed: %s", job_id, fb_provider, fb_exc)
        raise RuntimeError(format_runtime_provider_error(resolve_exc)) from resolve_exc


def _load_credential_pool(runtime: dict, job_id: str):
    runtime_provider = str(runtime.get("provider") or "").strip().lower()
    if not runtime_provider:
        return None
    try:
        from agent.credential_pool import load_pool
        pool = load_pool(runtime_provider)
        if pool.has_credentials():
            logger.info(
                "Job '%s': loaded credential pool for provider %s with %d entries",
                job_id, runtime_provider, len(pool.entries()))
            return pool
    except Exception as e:
        logger.debug("Job '%s': failed to load credential pool for %s: %s", job_id, runtime_provider, e)
    return None


def _init_cron_mcp_tools(job_id: str) -> None:
    """Register MCP servers for the agent's tool registry. Idempotent across ticks; non-fatal so a
    broken MCP server never kills a working job."""
    try:
        # Initialize MCP servers so configured mcp_servers are available to the agent's tool registry before
        # AIAgent is constructed. Without this, cron jobs never saw any MCP tools — only the gateway / CLI
        # paths called discover_mcp_tools() at startup. Idempotent: subsequent ticks short-circuit on
        # already-connected servers inside register_mcp_servers(). Non-fatal on failure: a broken MCP server
        # shouldn't kill an otherwise-working cron job. See #4219.
        from tools.mcp_tool_discovery import discover_mcp_tools
        _mcp_tools = discover_mcp_tools()
        if _mcp_tools:
            logger.info("Job '%s': %d MCP tool(s) available", job_id, len(_mcp_tools))
    except Exception as _mcp_exc:
        logger.warning("Job '%s': MCP initialization failed (non-fatal): %s", job_id, _mcp_exc)


def _open_cron_session_db(job: dict):
    """Open the SQLite session store under its own timeout (HERMES_CRON_TIMEOUT only watches
    run_conversation). A wedged sqlite3.connect returns None (no session store) instead of
    wedging the worker thread."""
    # Initialize the SQLite session store so cron job messages are persisted and discoverable via
    # session_search (same pattern as gateway/run.py) — only now, after every early-return path (wake-gate,
    # prompt validation, drift skip) has passed, so a gated run never opens state.db just to abandon the
    # handle (#96290). Bounded with its own timeout (separate from HERMES_CRON_TIMEOUT, which only watches
    # the agent's run_conversation below): SessionDB.__init__ opens/migrates state.db synchronously and has
    # no timeout of its own against a wedged sqlite3.connect (e.g. a stale flock left by a crashed sibling
    # process). An unbounded hang here would wedge the job's worker thread, so the init is bounded and a
    # timeout proceeds without a session store instead of blocking the run forever.
    _session_db_timeout = _sched._get_session_db_timeout()
    try:
        from hermes_state_registry import acquire

        if _session_db_timeout <= 0:
            return acquire()
        _session_db_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Copy the context so a profile run resolves ITS OWN home/state.db on the worker thread
        # instead of the process-global default.
        _session_db_context = contextvars.copy_context()
        _session_db_future = _session_db_pool.submit(_session_db_context.run, acquire)
        try:
            return _session_db_future.result(timeout=_session_db_timeout)
        except concurrent.futures.TimeoutError:
            # The abandoned worker may still finish; close its late result or its SQLite FDs leak.
            # The worker is abandoned (shutdown below doesn't wait for it). If SessionDB() later completes
            # inside it, the future's result would be orphaned and its SQLite FDs (.db, WAL, SHM) leak until
            # process exit. Register a done-callback that retrieves and closes any eventual late result
            # (#72782).
            _session_db_future.add_done_callback(_sched._close_late_session_db_result)
            raise
        finally:
            # Abandon a wedged connect() rather than blocking shutdown on it.
            _session_db_pool.shutdown(wait=False)
    except concurrent.futures.TimeoutError:
        logger.error(
            "Job '%s': SessionDB init did not return within %.0fs — proceeding "
            "without a session store for this run instead of blocking it forever",
            job.get("id", "?"), _session_db_timeout)
    except Exception as e:
        logger.debug("Job '%s': SQLite session store not available: %s", job.get("id", "?"), e)
    return None


@dataclass
class _CronAgentSetup:
    """Everything ``AIAgent(...)`` needs that is resolved from job + config (or a preflight block)."""
    blocked: Optional[_sched._RunResult] = None
    model: str = ""
    runtime: dict = None
    prefill_messages: Any = None
    max_iterations: Any = None
    reasoning_config: Any = None
    fallback_model: Any = None
    credential_pool: Any = None


def _resolve_cron_agent_setup(job: dict, job_id: str, job_name: str, jc) -> _CronAgentSetup:
    """Resolve model/runtime/reasoning/pool for the run, in the original gate order: exfil guard ->
    preflight (may block) -> runtime (+ fallback chain) -> credential pool -> MCP."""
    _cfg = jc.cfg
    setup = _CronAgentSetup(model=jc.model)
    setup.prefill_messages = _load_prefill_messages(_cfg, job_id)

    # resolve_turn_limit() honors none/unlimited (sys.maxsize) and explicit 0 / null.
    from hermes_cli.config import resolve_turn_limit as _resolve_turn_limit
    _mt = _cfg.get("agent", {}).get("max_turns")
    if _mt is None:
        _mt = _cfg.get("max_turns")
    setup.max_iterations = _resolve_turn_limit(_mt)

    # Runtime backstop (CWE-200/522): fail closed BEFORE resolution on a provider/base_url pair
    # that would ship a stored credential off-host; hand-written jobs bypass create-time checks.
    _sched._guard_job_credential_exfil(job)

    setup.blocked = _preflight_or_block(job, job_id, job_name, _cfg)
    if setup.blocked is not None:
        return setup

    setup.runtime, setup.model = _resolve_job_runtime(job, job_id, jc)
    setup.reasoning_config = _resolve_job_reasoning_config(
        job, _cfg if isinstance(_cfg, dict) else {}, str(setup.model)
    )
    setup.fallback_model = _sched.get_fallback_chain(_cfg) or None
    setup.credential_pool = _load_credential_pool(setup.runtime, job_id)
    # MCP servers must be registered before AIAgent is constructed.
    _init_cron_mcp_tools(job_id)
    # Only now can a requested MCP toolset be judged: its alias is process-global but its tools live
    # in this profile's registry overlay, and quiet_mode hides the empty resolution (#109050).
    if _sched._cron_preflight_enabled(_cfg):
        _mcp_reason = _sched._empty_requested_mcp_toolsets(job, _cfg)
        if _mcp_reason:
            setup.blocked = _blocked_config_result(job_id, job_name, _mcp_reason)
    return setup


def _construct_cron_agent(AIAgent, job: dict, _cfg: dict, setup: _CronAgentSetup, *, workdir, session_id, session_db):
    runtime = setup.runtime
    pr = _cfg.get("provider_routing") or {}
    return AIAgent(
        model=setup.model,
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        request_overrides=runtime.get("request_overrides"),
        acp_command=runtime.get("command"),
        acp_args=runtime.get("args"),
        max_iterations=setup.max_iterations,
        reasoning_config=setup.reasoning_config,
        prefill_messages=setup.prefill_messages,
        fallback_model=setup.fallback_model,
        credential_pool=setup.credential_pool,
        providers_allowed=pr.get("only"),
        providers_ignored=pr.get("ignore"),
        providers_order=pr.get("order"),
        provider_sort=pr.get("sort"),
        openrouter_min_coding_score=(_cfg.get("openrouter") or {}).get("min_coding_score"),
        enabled_toolsets=_sched._resolve_cron_enabled_toolsets(job, _cfg),
        disabled_toolsets=_resolve_cron_disabled_toolsets(_cfg, job),
        quiet_mode=True,
        # Project context files only with a configured workdir; SOUL.md always.
        skip_context_files=not bool(workdir),
        load_soul_identity=True,
        skip_memory=False,
        skip_background_review=True,  # Cron has no human-in-the-loop need for skill/memory review forks (~30K tok/event)
        platform="cron",
        session_id=session_id,
        session_db=session_db,
    )


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.scheduler`` re-exports from it.
from cron import scheduler as _sched  # noqa: E402
