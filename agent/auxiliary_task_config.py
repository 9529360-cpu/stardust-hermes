"""Per-task configuration: provider/model resolution for a task, timeouts, extra body,
the compression fast lane and per-task concurrency semaphores.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


# Aliases for direct REST APIs not modeled in PROVIDER_REGISTRY, so ``auxiliary.<task>.provider:
# openai`` resolves to a working ``custom`` endpoint (OPENAI_API_KEY + api.openai.com) instead of
# silently falling back to the main provider and sending OpenAI model names elsewhere.
_AUX_DIRECT_API_BASE_URLS: Dict[str, str] = {"openai": "https://api.openai.com/v1"}


# MoA virtual provider: an *explicit* `provider: moa` override (either the caller-passed `provider` arg or
# `auxiliary.<task>.provider` in config.yaml) reaches this function directly — it never goes through
# _resolve_auto_route(), which only unwraps the *implicit* "main provider is moa" case (#53827). Left as-is, "moa"
# is returned verbatim and resolve_provider_client() looks it up in PROVIDER_REGISTRY (which has no "moa"
# entry — it's not a real HTTP provider), falls to the unknown-provider dead end, and call_llm surfaces a
# nonsensical "MOA_API_KEY environment variable" error for a provider that was never meant to be reached
# over the wire. Auxiliary tasks don't need the reference fan-out — resolve to the preset's aggregator slot
# instead, exactly like the implicit path does (shared helper: _resolve_moa_aggregator).
def _unwrap_moa_provider(prov: str, mdl: Optional[str]) -> Tuple[str, Optional[str]]:
    """Resolve an *explicit* ``provider: moa`` to its preset's aggregator slot (_resolve_auto_route()
    only unwraps the implicit case; "moa" isn't in PROVIDER_REGISTRY and would dead-end)."""
    if prov.strip().lower() != "moa":
        return prov, mdl
    agg_provider, agg_model = _aux._resolve_moa_aggregator(mdl)
    if agg_provider and agg_model:
        return agg_provider, agg_model
    return prov, mdl


def _expand_direct_api_alias(prov: Optional[str], existing_base: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``provider: openai`` → custom + the user's OpenAI endpoint, api.openai.com/v1 only as the last resort.

    A ``providers.openai`` entry keeps the provider name so the named-custom branch applies its base_url and
    key; otherwise ``OPENAI_BASE_URL`` (a proxy/gateway the OPENAI_API_KEY was issued for) wins over the
    public endpoint — sending the proxy key to api.openai.com 401s and then quarantines a valid key.
    """
    if not prov:
        return prov, existing_base
    target_base = _AUX_DIRECT_API_BASE_URLS.get(prov.strip().lower())
    if target_base is None:
        return prov, existing_base
    with contextlib.suppress(Exception):
        from hermes_cli.runtime_provider import _get_named_custom_provider
        if _get_named_custom_provider(prov) is not None:
            return prov, existing_base
    return "custom", existing_base or _aux._scoped_key_env("OPENAI_BASE_URL").rstrip("/") or target_base


def _preserve_provider_with_base_url(prov: Optional[str]) -> bool:
    """True when a first-class provider keeps its identity alongside an explicit base_url."""
    normalized = str(prov or "").strip().lower()
    if normalized in {"", "auto", "custom"} or normalized.startswith("custom:"):
        return False
    try:
        from hermes_cli.providers import get_provider
        return get_provider(normalized) is not None
    except Exception:  # keep provider-backed routes safe when the catalog can't load
        return normalized in {
            "anthropic", "copilot", "copilot-acp", "minimax-oauth", "nous", "openai-codex", "qwen-oauth", "xai-oauth",
        }


def _resolve_task_provider_model(
    task: str = None, provider: str = None, model: str = None, base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Determine (provider, model, base_url, api_key, api_mode) for a call.

    Priority: explicit args > config auxiliary.{task}.* > "auto". A bare base_url means custom,
    but a first-class provider + base_url keeps the provider identity so its auth/transport
    shaping still applies. api_mode is "chat_completions", "codex_responses", or None (auto).
    """
    cfg_provider = cfg_model = cfg_base_url = cfg_api_key = resolved_api_mode = None
    if task:
        task_config = _aux._get_auxiliary_task_config(task)
        cfg_provider = str(task_config.get("provider", "")).strip() or None
        cfg_model = str(task_config.get("model", "")).strip() or None
        cfg_base_url = str(task_config.get("base_url", "")).strip() or None
        cfg_api_key = str(task_config.get("api_key", "")).strip() or None
        if not cfg_api_key:  # key_env → env var when api_key is not set directly
            cfg_key_env = str(task_config.get("key_env") or task_config.get("api_key_env") or "").strip()
            if cfg_key_env:
                cfg_api_key = _aux._scoped_key_env(cfg_key_env) or None
        resolved_api_mode = str(task_config.get("api_mode", "")).strip() or None
    # 'auto' is a sentinel ("inherit / auto-detect"), not a model id — leaking it to the wire
    # yields a 200 with an error-text body that consumers accept as output. The explicit `model`
    # kwarg needs the same normalization: MoA slots forward preset `model:` fields through it.
    if model and model.lower() == "auto":
        model = None
    if cfg_model and cfg_model.lower() == "auto":
        cfg_model = None
    resolved_model = model or cfg_model
    # Any moa:// facade endpoint belongs to the facade, not the aggregator's real provider —
    # drop it (mirrors _resolve_auto_route()).
    if provider and str(provider).strip().lower() == "moa":
        provider, resolved_model = _unwrap_moa_provider(provider, resolved_model)
        if provider and provider.lower() != "moa":
            base_url = None
            api_key = None
    elif cfg_provider and str(cfg_provider).strip().lower() == "moa":
        cfg_provider, cfg_model = _unwrap_moa_provider(cfg_provider, resolved_model)
        if cfg_provider and cfg_provider.lower() != "moa":
            resolved_model = cfg_model
            cfg_base_url = None
            cfg_api_key = None
    if provider:
        provider, base_url = _expand_direct_api_alias(provider, base_url)
    if cfg_provider:
        cfg_provider, cfg_base_url = _expand_direct_api_alias(cfg_provider, cfg_base_url)
    # An explicit provider without base_url adopts the task's configured endpoint (same or
    # unnamed provider) so the early return below carries it. Explicit "auto" is excluded — it
    # must keep flowing through auto-resolution.
    # See #58515.
    if provider and provider != "auto" and not base_url and cfg_base_url and cfg_provider in (None, provider):
        base_url = cfg_base_url
        if not api_key:
            api_key = cfg_api_key
    if base_url:
        kept = provider if _preserve_provider_with_base_url(provider) else "custom"
        return kept, resolved_model, base_url, api_key, resolved_api_mode
    if provider:
        return provider, resolved_model, base_url, api_key, resolved_api_mode
    if cfg_base_url and cfg_api_key:
        return "custom", resolved_model, cfg_base_url, cfg_api_key, resolved_api_mode
    if cfg_base_url and cfg_provider and cfg_provider != "auto":
        # base_url without api_key: keep the provider so it can resolve credentials from env
        # vars instead of locking into "custom".
        return cfg_provider, resolved_model, cfg_base_url, None, resolved_api_mode
    if cfg_provider and cfg_provider != "auto":
        return cfg_provider, resolved_model, cfg_base_url, cfg_api_key, resolved_api_mode
    return "auto", resolved_model, None, None, resolved_api_mode


_DEFAULT_AUX_TIMEOUT = 30.0

# Reasoning compression models can exceed the default 120 s config timeout, falling back to the
# deterministic marker. Bounded *floor* for config-derived compression timeouts only; never
# overrides an explicit per-call timeout.
# Compression summarises large conversation histories; a reasoning auxiliary model (e.g. Codex / GPT-5.5)
# can legitimately take longer than the default ``auxiliary.compression.timeout`` (120 s), causing the
# stream to time out and the compressor to fall back to the deterministic context marker (#54915). A floor
# is harmless for fast compression models (they finish before the deadline) and is a minimum, so a higher
# config value is kept unchanged.
_COMPRESSION_TIMEOUT_FLOOR_SECONDS = 300.0


def _get_auxiliary_task_config(task: str) -> Dict[str, Any]:
    """Config dict for auxiliary.<task>, or {} when unavailable. Plugin-registered tasks get their
    declared defaults layered under user config (user wins); built-in defaults live in DEFAULT_CONFIG."""
    if not task:
        return {}
    try:
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly()
    except ImportError:
        return {}
    aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
    if not isinstance(task_config, dict):
        task_config = {}
    try:
        from hermes_cli.plugins import get_plugin_auxiliary_tasks
        for _entry in get_plugin_auxiliary_tasks():
            if _entry.get("key") == task:
                _defaults = _entry.get("defaults") or {}
                if isinstance(_defaults, dict):
                    return {**_defaults, **task_config}
                break
    except Exception:
        pass  # plugin discovery failure must not break aux task config reads
    return task_config


class CompressionFastLane(NamedTuple):
    """Explicit, non-reasoning compression route."""

    certified_non_reasoning: bool
    reasoning_config: Optional[Dict[str, Any]]


def _fast_lane_config_fields(config: Dict[str, Any]) -> tuple[str, str, bool]:
    """Only explicit reasoning disablement certifies a non-reasoning route."""
    from hermes_constants import parse_reasoning_effort
    provider = str(config.get("provider") or "").strip().lower()
    model = str(config.get("model") or "").strip()
    parsed_effort = parse_reasoning_effort(config.get("reasoning_effort"))
    non_reasoning = parsed_effort is not None and parsed_effort.get("enabled") is False
    return provider, model, non_reasoning


def resolve_compression_fast_lane(
    actual_provider: str, actual_model: Optional[str], *, requested_provider: Optional[str] = None,
    requested_model: Optional[str] = None, route_config: Optional[Dict[str, Any]] = None,
) -> CompressionFastLane:
    """Certify explicit non-reasoning settings only on the matching destination."""
    config = route_config if route_config is not None else _aux._get_auxiliary_task_config("compression")
    cfg_provider, cfg_model, non_reasoning = _fast_lane_config_fields(config)
    provider = str(requested_provider or "").strip().lower() or cfg_provider
    model = str(requested_model or "").strip() or cfg_model
    explicit_route = provider not in {"", "auto"} and model.lower() not in {"", "auto"}
    actual_norm = _aux._normalize_aux_provider(_aux._fallback_provider_from_label(str(actual_provider or "")))
    provider_matches = actual_norm == _aux._normalize_aux_provider(provider)
    model_matches = str(actual_model or "").strip().lower() == model.lower()
    if explicit_route and provider_matches and model_matches and non_reasoning:
        return CompressionFastLane(True, {"enabled": False, "effort": "none"})
    return CompressionFastLane(False, None)


def _compression_config_claims_fast_lane(config: Dict[str, Any]) -> bool:
    """Whether task config declares fast-only controls that cannot leak."""
    provider, model, non_reasoning = _fast_lane_config_fields(config)
    return provider not in {"", "auto"} and model.lower() not in {"", "auto"} and non_reasoning


def _compression_fast_lane_controls(
    task: str | None, *, actual_provider: str, actual_model: str | None,
    requested_provider: str | None, requested_model: str | None, route_config: Dict[str, Any],
    leak_guard_config: Dict[str, Any], max_tokens: int | None, extra_body: Dict[str, Any],
) -> tuple[int | None, Dict[str, Any]]:
    """Apply the certified compression controls to one resolved route."""
    if task != "compression" or max_tokens is not None:
        return max_tokens, extra_body
    body = dict(extra_body)
    lane = resolve_compression_fast_lane(
        actual_provider, actual_model, requested_provider=requested_provider, requested_model=requested_model, route_config=route_config,
    )
    if lane.reasoning_config is not None:
        if "reasoning" not in body:
            body["reasoning"] = lane.reasoning_config
    elif _compression_config_claims_fast_lane(leak_guard_config):
        body.pop("reasoning", None)
    return max_tokens, body


def _get_task_timeout(task: str, default: float = _DEFAULT_AUX_TIMEOUT) -> float:
    """``auxiliary.<task>.timeout`` from config, else *default*."""
    if not task:
        return default
    raw = _aux._get_auxiliary_task_config(task).get("timeout")
    if raw is not None:
        with contextlib.suppress(ValueError, TypeError):
            return float(raw)
    return default


def _effective_aux_timeout(task: str, timeout: Optional[float]) -> float:
    """Explicit ``timeout`` wins, else config; compression gets a floor so a reasoning model
    summarising a large context isn't cut off."""
    if timeout is not None:
        return timeout
    effective = _aux._get_task_timeout(task)
    return max(effective, _COMPRESSION_TIMEOUT_FLOOR_SECONDS) if task == "compression" else effective


def _get_task_extra_body(task: str) -> Dict[str, Any]:
    """Shallow copy of ``auxiliary.<task>.extra_body`` with ``reasoning_effort`` folded into
    ``reasoning`` unless one is configured (more specific wins). MoA tasks are excluded: their
    reasoning depth is per-slot in the preset."""
    task_config = _aux._get_auxiliary_task_config(task)
    raw = task_config.get("extra_body")
    result = dict(raw) if isinstance(raw, dict) else {}
    if "reasoning" in result:
        return result
    effort = task_config.get("reasoning_effort")
    if effort is None or effort == "":
        return result
    if task in ("moa_reference", "moa_aggregator"):
        logger.warning(
            "auxiliary.%s.reasoning_effort is not supported — MoA reasoning depth is per-slot: set reasoning_effort "
            "on the preset's reference_models entries / aggregator instead (moa.presets.<name>...). Ignoring.",
            task,
        )
        return result
    from hermes_constants import parse_reasoning_effort
    parsed = parse_reasoning_effort(effort)
    if parsed is not None:
        result["reasoning"] = parsed
    else:
        logger.warning(
            "auxiliary.%s.reasoning_effort %r is not a valid level (none, minimal, low, medium, high, xhigh, max, ultra) — ignoring",
            task, effort,
        )
    return result


# Per-task concurrency limiting: many sessions can spawn unbounded background aux calls, each
# retrying across the fallback chain during incidents.
# During provider incidents each call also retries / fans out across the fallback chain, multiplying request
# volume on already-degraded endpoints. A per-task semaphore caps in-flight calls so retry amplification
# stays bounded. See #23324.
# Keyed by profile home as well: the limit is the profile's ``auxiliary.<task>.max_concurrency``, and two
# multiplexed profiles with different limits would otherwise rebuild (and reset) one shared semaphore.
_aux_sync_semaphores: Dict[Tuple[str, str], Tuple[int, threading.BoundedSemaphore]] = {}
_aux_async_semaphores: Dict[Tuple[str, str, int], Tuple[int, Any]] = {}
_aux_sem_lock = threading.Lock()


def _get_task_max_concurrency(task: Optional[str]) -> Optional[int]:
    """``auxiliary.<task>.max_concurrency`` as a positive int, or None. Vision uses this key for
    its encode/resize CPU pool; its LLM calls stay concurrent."""
    if not task or task == "vision":
        return None
    try:
        value = int(_aux._get_auxiliary_task_config(task).get("max_concurrency"))
    except (TypeError, ValueError):  # missing (None) or malformed
        return None
    return value if value > 0 else None


def _cached_semaphore(store: dict, key: Any, limit: int, factory: Callable[[int], Any]) -> Any:
    """Return the cached semaphore for ``key``, rebuilding it when the limit changed."""
    with _aux_sem_lock:
        entry = store.get(key)
        if entry is None or entry[0] != limit:
            store[key] = entry = (limit, factory(limit))
        return entry[1]


def _acquire_sync_aux_semaphore(task: Optional[str]) -> Optional[threading.BoundedSemaphore]:
    """Get a per-task sync semaphore, rebuilding it after a config change."""
    limit = _get_task_max_concurrency(task)
    if limit is None:
        return None
    from hermes_constants import hermes_home_key
    return _cached_semaphore(_aux_sync_semaphores, (hermes_home_key(), task), limit, threading.BoundedSemaphore)


def _acquire_async_aux_semaphore(task: Optional[str]):
    """Get a per-task, per-event-loop async semaphore after config lookup."""
    limit = _get_task_max_concurrency(task)
    if limit is None:
        return None
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    from hermes_constants import hermes_home_key
    return _cached_semaphore(_aux_async_semaphores, (hermes_home_key(), task, id(loop)), limit, asyncio.Semaphore)


def _reset_aux_semaphores() -> None:
    """Drop cached semaphores (test helper)."""
    with _aux_sem_lock:
        _aux_sync_semaphores.clear()
        _aux_async_semaphores.clear()


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
