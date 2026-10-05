"""Recovery for a failed auxiliary call: credential-pool recovery and credential refresh,
same-provider retry, payment fallback, the configured and main-agent fallback chains, and
the main-provider / discovery routes the auto chain tries first.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


def _pool_cache_hint(provider: str, *, main_runtime: Optional[Dict[str, Any]] = None) -> str:
    """Return a stable cache discriminator for pooled providers."""
    normalized = _aux._normalize_aux_provider(provider)
    if normalized == "auto":
        runtime = _aux._normalize_main_runtime(main_runtime)
        normalized = _aux._normalize_aux_provider(runtime.get("provider") or _aux._read_main_provider())
    if normalized in {"", "auto", "custom"}:
        return ""
    entry = _aux._peek_pool_entry(normalized)
    if entry is None:
        return ""
    entry_id = str(getattr(entry, "id", "") or "").strip()
    return f"{normalized}:{entry_id}" if entry_id else ""


# Ordered (host, provider) tables for inferring a backend from a client base URL.
_POOL_PROVIDER_BY_HOST = (
    ("chatgpt.com", "openai-codex"), ("openrouter.ai", "openrouter"),
    ("inference-api.nousresearch.com", "nous"), ("api.anthropic.com", "anthropic"),
    ("githubcopilot.com", "copilot"), ("api.kimi.com", "kimi-coding"), ("api.x.ai", "xai-oauth"),
)
_AUTH_REFRESH_PROVIDER_BY_HOST = (
    ("api.githubcopilot.com", "copilot"), ("chatgpt.com", "openai-codex"),
    ("api.anthropic.com", "anthropic"), ("inference-api.nousresearch.com", "nous"),
)


def _provider_for_host(base_url: str, table: Tuple[Tuple[str, str], ...]) -> Optional[str]:
    """First provider in ``table`` whose host matches ``base_url``, else None."""
    for host, provider in table:
        if _aux.base_url_host_matches(base_url, host):
            return provider
    return None


def _recoverable_pool_provider(
    resolved_provider: str, client: Any, main_runtime: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Infer which provider pool can recover the current auxiliary client.
    None when the client targets a different host than the session's configured endpoint for that
    provider: a rejection there says nothing about the key, so rotating/quarantining it would kill a
    working credential (Miho report — proxy users)."""
    normalized = _aux._normalize_aux_provider(resolved_provider)
    base = str(getattr(client, "base_url", "") or "")
    runtime = _aux._normalize_main_runtime(main_runtime)
    rt_base = str(runtime.get("base_url") or "")
    rt_key = runtime.get("api_key")
    client_key = getattr(client, "api_key", None)
    # Only the SESSION's own key is shielded, and only when it was sent somewhere other than the
    # session's origin (scheme+host+port — a port or HTTPS→HTTP change is a different trust boundary).
    # An independently owned auxiliary pool keeps rotating at its own origin.
    if (base and rt_base and normalized == runtime.get("provider")
            and isinstance(rt_key, str) and rt_key and client_key == rt_key
            and _aux.base_url_origin(base) != _aux.base_url_origin(rt_base)):
        logger.info("Auxiliary: %s rejected the session key at %s, but the session's endpoint is %s — "
                    "endpoint mismatch, not a dead key; skipping credential rotation",
                    normalized, _aux.base_url_hostname(base), _aux.base_url_hostname(rt_base))
        return None
    if normalized not in {"", "auto", "custom"}:
        return normalized
    known = _provider_for_host(base, _POOL_PROVIDER_BY_HOST)
    if known is not None:
        return known
    # Providers outside the table (e.g. opencode-go): match base URL against registered
    # api_key providers so pool rotation works for them too.
    if main_runtime:
        runtime = _aux._normalize_main_runtime(main_runtime)
        rt_provider = runtime.get("provider", "")
        if rt_provider and rt_provider not in {"", "auto", "custom"}:
            with contextlib.suppress(Exception):
                from hermes_cli.auth import PROVIDER_REGISTRY
                pconfig = PROVIDER_REGISTRY.get(rt_provider)
                if pconfig and getattr(pconfig, "auth_type", None) == "api_key":
                    # The pool's key was issued for the endpoint the main runtime actually uses; a
                    # rejection at any other host (registry default vs configured proxy) says nothing
                    # about that key, so it must not be marked exhausted.
                    rt_base = str(runtime.get("base_url") or getattr(pconfig, "inference_base_url", "") or "").rstrip("/")
                    if rt_base and _aux.base_url_host_matches(base, _aux.base_url_hostname(rt_base)):
                        return rt_provider
    return None


def _recover_provider_pool(provider: str, exc: Exception, *, failed_api_key: str = "") -> bool:
    """Try same-provider credential-pool recovery for auxiliary calls.

    ``failed_api_key`` lets mark_exhausted_and_rotate identify the right pool entry even if
    another process already rotated (current() would be None).
    """
    normalized = _aux._normalize_aux_provider(provider)
    try:
        pool = _aux.load_pool(normalized)
    except Exception as load_exc:
        logger.debug("Auxiliary client: could not load pool for %s recovery: %s", normalized, load_exc)
        return False
    if not pool or not pool.has_credentials():
        return False
    status_code = getattr(exc, "status_code", None)

    def _rotate(fallback_status: int) -> bool:
        error_context: Dict[str, Any] = {"message": str(exc)}
        if status_code is not None:
            error_context["status_code"] = status_code
        next_entry = pool.mark_exhausted_and_rotate(
            status_code=status_code if status_code is not None else fallback_status,
            error_context=error_context, api_key_hint=failed_api_key or None,
        )
        if next_entry is None:
            return False
        _aux._evict_cached_clients(normalized)
        return True

    if _aux._is_auth_error(exc):
        if pool.try_refresh_current() is not None:
            _aux._evict_cached_clients(normalized)
            return True
        return _rotate(401)
    if _aux._is_payment_error(exc):
        return _rotate(402)
    if _aux._is_rate_limit_error(exc):
        return _rotate(429)
    return False


def _prepare_same_provider_retry(
    *, task: Optional[str], resolved_provider: str, resolved_model: Optional[str],
    resolved_base_url: Optional[str], resolved_api_key: Optional[str],
    resolved_api_mode: Optional[str], main_runtime: Optional[Dict[str, Any]],
    final_model: Optional[str], messages: list, temperature: Optional[float],
    max_tokens: Optional[int], tools: Optional[list], effective_timeout: float,
    effective_extra_body: dict, reasoning_config: Optional[dict], async_mode: bool,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Rebuild (client, request kwargs) for a same-provider retry after credential recovery."""
    if task == "vision":
        effective_provider, retry_client, retry_model = _aux.resolve_vision_provider_client(
            provider=resolved_provider, model=final_model, base_url=resolved_base_url,
            api_key=resolved_api_key, async_mode=async_mode,
        )
    else:
        retry_client, retry_model = _aux._get_cached_client(
            resolved_provider, resolved_model, async_mode=async_mode, base_url=resolved_base_url,
            api_key=resolved_api_key, api_mode=resolved_api_mode, main_runtime=main_runtime,
        )
        effective_provider = _aux._effective_provider_for_client(retry_client, resolved_provider)
    if retry_client is None:
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: provider {resolved_provider} could not be rebuilt after recovery"
        )
    retry_base = str(getattr(retry_client, "base_url", "") or "")
    retry_kwargs = _aux._build_call_kwargs(
        effective_provider or resolved_provider, retry_model or final_model, messages,
        temperature=temperature, max_tokens=max_tokens, tools=tools, timeout=effective_timeout,
        extra_body=effective_extra_body, reasoning_config=reasoning_config,
        base_url=retry_base or resolved_base_url, task=task,
    )
    # Preserve per-request attribution headers (e.g. Copilot ``x-initiator``) so the retry keeps capability gating.
    if extra_headers:
        # Copilot's ``x-initiator: user``) across the rebuilt-client retry — dropping them here would let a
        # recovery retry silently lose capability gating (#60293).
        # Preserve per-request attribution headers across the rebuilt-client retry — see the sync variant
        # above (#60293).
        retry_kwargs["extra_headers"] = dict(extra_headers)
    if _aux._is_anthropic_compat_endpoint(resolved_provider, retry_base):
        retry_kwargs["messages"] = _aux._convert_openai_images_to_anthropic(retry_kwargs["messages"])
    return retry_client, retry_kwargs


def _retry_same_provider_sync(*, resolved_provider: str, resolved_api_mode: Optional[str], task: Optional[str], **prep) -> Any:
    retry_client, retry_kwargs = _prepare_same_provider_retry(
        task=task, resolved_provider=resolved_provider, resolved_api_mode=resolved_api_mode, async_mode=False, **prep,
    )
    return _aux._validate_llm_response(
        _aux._relay_sync_completion(retry_client, retry_kwargs, provider=resolved_provider, api_mode=resolved_api_mode), task,
    )


async def _retry_same_provider_async(*, resolved_provider: str, resolved_api_mode: Optional[str], task: Optional[str], **prep) -> Any:
    retry_client, retry_kwargs = _prepare_same_provider_retry(
        task=task, resolved_provider=resolved_provider, resolved_api_mode=resolved_api_mode, async_mode=True, **prep,
    )
    return _aux._validate_llm_response(
        await _aux._relay_async_completion(retry_client, retry_kwargs, provider=resolved_provider, api_mode=resolved_api_mode),
        task,
    )


def _creds_have_api_key(creds: Dict[str, Any]) -> bool:
    return bool(str(creds.get("api_key", "") or "").strip())


def _refresh_copilot_credentials() -> bool:
    from hermes_cli.copilot_auth import _jwt_cache, _token_fingerprint, exchange_copilot_token, resolve_copilot_token
    raw_token, _source = resolve_copilot_token()
    if not str(raw_token or "").strip():
        return False
    _jwt_cache.pop(_token_fingerprint(raw_token), None)
    exchange_copilot_token(raw_token)
    return True


def _refresh_codex_credentials() -> bool:
    from hermes_cli.auth import resolve_codex_runtime_credentials
    return _creds_have_api_key(resolve_codex_runtime_credentials(force_refresh=True))


def _refresh_nous_credentials() -> bool:
    from hermes_cli.auth import resolve_nous_runtime_credentials
    return _creds_have_api_key(resolve_nous_runtime_credentials(
        timeout_seconds=_aux.env_float("HERMES_NOUS_TIMEOUT_SECONDS", 15), force_refresh=True
    ))


def _refresh_anthropic_credentials(failed_api_key: str = "") -> bool:
    from agent.anthropic_credentials import read_claude_code_credentials, _refresh_oauth_token
    token = failed_api_key
    if not token:
        return False
    pool = _aux.load_pool("anthropic")
    if pool.entry_id_for_api_key(token):
        return pool.try_refresh_matching(api_key_hint=token) is not None
    creds = read_claude_code_credentials()
    # Never spend an ambient login's refresh rotation for another request's key.
    if isinstance(creds, dict) and creds.get("accessToken") == token and creds.get("refreshToken"):
        return bool(_refresh_oauth_token(creds))
    return False


def _refresh_xai_oauth_credentials() -> bool:
    """Pool-level refresh first, then the singleton auth-store resolver."""
    pool = _aux.load_pool("xai-oauth")
    if pool and pool.has_credentials():
        pool.select()
        refreshed = pool.try_refresh_current()
        if refreshed is not None and str(getattr(refreshed, "runtime_api_key", "") or "").strip():
            return True
    from hermes_cli.auth import resolve_xai_oauth_runtime_credentials
    return _creds_have_api_key(resolve_xai_oauth_runtime_credentials(force_refresh=True))


def _refresh_vertex_credentials() -> bool:
    """Mirrors run_agent's Vertex refresh; the cache key ignores the rotating bearer, so
    without the eviction that follows, a ~1h-expired aux Vertex client 401s forever."""
    from agent.vertex_adapter import get_vertex_config
    token, base_url = get_vertex_config()
    return bool(isinstance(token, str) and token.strip() and isinstance(base_url, str) and base_url.strip())


# Each refresher returns True when a usable credential exists; the caller then evicts cached clients.
_CREDENTIAL_REFRESHERS: Dict[str, Callable[..., bool]] = {
    "copilot": _refresh_copilot_credentials, "openai-codex": _refresh_codex_credentials,
    "nous": _refresh_nous_credentials, "anthropic": _refresh_anthropic_credentials,
    "xai-oauth": _refresh_xai_oauth_credentials, "vertex": _refresh_vertex_credentials,
}


def _refresh_provider_credentials(provider: str, *, failed_api_key: str = "") -> bool:
    """Refresh short-lived credentials for OAuth-backed auxiliary providers."""
    normalized = _aux._normalize_aux_provider(provider)
    refresher = _CREDENTIAL_REFRESHERS.get(normalized)
    if refresher is None:
        return False
    try:
        if not (refresher(failed_api_key) if normalized == "anthropic" else refresher()):
            return False
        _aux._evict_cached_clients(normalized)
        return True
    except Exception as exc:
        logger.debug("Auxiliary provider credential refresh failed for %s: %s", normalized, exc)
        return False


def _auth_refresh_provider_for_route(resolved_provider: Optional[str], client_base_url: str) -> str:
    """Provider whose short-lived credentials should be refreshed; auto-routed calls keep
    ``resolved_provider == "auto"``, so infer the backend from the client's base URL."""
    normalized = _aux._normalize_aux_provider(resolved_provider)
    if normalized and normalized != "auto":
        return normalized
    return _provider_for_host(client_base_url, _AUTH_REFRESH_PROVIDER_BY_HOST) or normalized


def _fallback_chain_entry(task: Optional[str], fb_label: str) -> Optional[Dict[str, Any]]:
    """Resolve the ``fallback_chain`` entry a ``fallback_chain[<i>](<provider>)`` label points at,
    or None when the label is not a configured-chain candidate or the index no longer resolves."""
    if not task or not fb_label:
        return None
    m = re.match(r"fallback_chain\[(\d+)\]", fb_label)
    if not m:
        return None
    try:
        chain = _aux._get_auxiliary_task_config(task).get("fallback_chain")
        entry = chain[int(m.group(1))] if isinstance(chain, list) else None
    except Exception:
        return None
    return entry if isinstance(entry, dict) else None


def _coerce_positive_timeout(raw: Any) -> Optional[float]:
    """Coerce a config ``timeout`` to a positive float, or None (rejects bools, which are ints)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return None


def _fallback_entry_timeout(task: Optional[str], fb_label: str) -> Optional[float]:
    """Per-entry ``timeout`` for a configured fallback candidate, or None (keep the task-level
    timeout). Inheriting the primary's deadline used to kill healthy-but-slower fallbacks.

    A fallback candidate previously inherited the exact timeout the primary provider was called with. When
    that deadline was tuned for the primary (or the primary simply consumed its whole budget before failing
    over), the fallback aborted on the same clock even when independently healthy — a 163k-token compression
    that needs ~90s on the fallback died at the primary's 30s deadline every turn (#62452).
    """
    entry = _fallback_chain_entry(task, fb_label)
    return _coerce_positive_timeout(entry.get("timeout") if entry else None)


def _fallback_provider_from_label(label: str) -> str:
    """Recover the provider identifier from a fallback display label."""
    match = re.match(r"(?:fallback_chain\[\d+\]|fallback_providers\[\d+\]|main-agent)\(([^)]+)\)$", label or "")
    return match.group(1).strip() if match else str(label or "").strip()


class _FallbackDestination(NamedTuple):
    provider: str
    base_url: str
    api_mode: Optional[str]
    model: Optional[str]


def _complete_fallback_destination(
    provider: str, base_url: str, api_mode: Optional[str], model: Optional[str]
) -> _FallbackDestination:
    if not api_mode:
        if _aux._endpoint_speaks_anthropic_messages(base_url):
            api_mode = "anthropic_messages"
        else:
            with contextlib.suppress(Exception):
                from hermes_cli.runtime_provider import resolve_runtime_provider
                runtime = resolve_runtime_provider(
                    requested=provider, explicit_base_url=base_url or None, target_model=model or ""
                )
                api_mode = str(runtime.get("api_mode") or "").strip() or None
    return _FallbackDestination(provider, base_url, api_mode, model)


def _fallback_destination_from_entry(
    entry: Dict[str, Any], fb_client: Any, fb_model: Optional[str]
) -> _FallbackDestination:
    provider = str(entry.get("provider") or "").strip()
    base_url = str(entry.get("base_url") or getattr(fb_client, "base_url", "") or "").strip()
    api_mode = str(entry.get("api_mode") or entry.get("transport") or "").strip() or None
    model = fb_model or str(entry.get("model") or "").strip() or None
    return _complete_fallback_destination(provider, base_url, api_mode, model)


def _fallback_destination(
    task: Optional[str], fb_client: Any, fb_model: Optional[str], fb_label: str
) -> _FallbackDestination:
    """Route identity of a fallback request: attached destination, else configured entry, else label."""
    attached = getattr(fb_client, "_hermes_fallback_destination", None)
    if isinstance(attached, _FallbackDestination):
        return attached
    entry = _fallback_chain_entry(task, fb_label)
    if entry is not None:
        return _fallback_destination_from_entry(entry, fb_client, fb_model)
    return _complete_fallback_destination(
        _fallback_provider_from_label(fb_label), str(getattr(fb_client, "base_url", "") or ""), None, fb_model,
    )


def _replan_synchronous_cache_sections(
    messages: list, tools: Optional[list], *, destination: _FallbackDestination
) -> tuple[list, list]:
    """Strip source decoration and plan one synchronous destination locally."""
    from agent.agent_runtime_helpers import configured_cache_ttl, plan_cache_sections_for_destination
    return plan_cache_sections_for_destination(
        messages, tools, provider=destination.provider, base_url=destination.base_url,
        api_mode=destination.api_mode or "", model=destination.model or "",
        # Operator's configured TTL so fallbacks don't regress 1h → 5m default (no live agent here; read config).
        cache_ttl=configured_cache_ttl(),
    )


def _fallback_request_kwargs(
    destination: _FallbackDestination, *, task: Optional[str], messages: list,
    tools: Optional[list], temperature: Optional[float], max_tokens: Optional[int],
    effective_timeout: float, effective_extra_body: dict, reasoning_config: Optional[dict],
    fallback_entry: dict, task_config: dict, apply_fast_lane: bool,
) -> Dict[str, Any]:
    """Build request kwargs for one fallback destination (cache-section replan + fast-lane cap)."""
    fallback_max_tokens, fallback_extra_body = max_tokens, effective_extra_body
    if apply_fast_lane:
        fallback_max_tokens, fallback_extra_body = _aux._compression_fast_lane_controls(
            task, actual_provider=destination.provider, actual_model=destination.model,
            requested_provider=fallback_entry.get("provider"),
            requested_model=fallback_entry.get("model"), route_config=fallback_entry,
            leak_guard_config=task_config, max_tokens=max_tokens, extra_body=effective_extra_body,
        )
    fallback_messages, fallback_tools = _replan_synchronous_cache_sections(messages, tools, destination=destination)
    fb_kwargs = _aux._build_call_kwargs(
        destination.provider, destination.model, fallback_messages,
        temperature=temperature, max_tokens=fallback_max_tokens, tools=fallback_tools, timeout=effective_timeout,
        extra_body=fallback_extra_body, reasoning_config=reasoning_config, base_url=destination.base_url, task=task)
    return fb_kwargs


def _plan_fallback_candidate(
    fb_client: Any, fb_model: Optional[str], fb_label: str, *, task: Optional[str],
    effective_timeout: float, apply_fast_lane: bool, **request,
) -> Tuple[_FallbackDestination, Dict[str, Any], Callable[[str, Any, Optional[str]], Dict[str, Any]]]:
    """Resolve the destination + first-attempt kwargs for a fallback candidate.

    Returns ``(destination, kwargs, rebuild)`` where ``rebuild(provider, client, model)`` produces
    kwargs for the credential-refreshed retry destination. A configured-chain entry's own
    ``timeout`` overrides ``effective_timeout``.
    """
    fb_timeout = _fallback_entry_timeout(task, fb_label)
    if fb_timeout is not None and fb_timeout != effective_timeout:
        logger.info(
            "Auxiliary %s: %s using its configured timeout %.0fs "
            "(task-level was %.0fs)",
            task or "call", fb_label, fb_timeout, effective_timeout,
        )
        effective_timeout = fb_timeout
    destination = _fallback_destination(task, fb_client, fb_model, fb_label)
    task_config = _aux._get_auxiliary_task_config(task) if task == "compression" else {}
    fallback_entry = _fallback_chain_entry(task, fb_label) or {}
    common = dict(
        task=task, effective_timeout=effective_timeout, fallback_entry=fallback_entry,
        task_config=task_config, apply_fast_lane=apply_fast_lane, **request,
    )

    def _rebuild(provider: str, client: Any, model: Optional[str]) -> Tuple[_FallbackDestination, Dict[str, Any]]:
        retry_destination = _FallbackDestination(
            provider, destination.base_url or str(getattr(client, "base_url", "") or ""),
            destination.api_mode, model or destination.model,
        )
        return retry_destination, _fallback_request_kwargs(retry_destination, **common)

    return destination, _fallback_request_kwargs(destination, **common), _rebuild


def _quarantine_fallback_candidate(
    task: Optional[str], fb_label: str, fb_provider: str, fb_err: Exception, *,
    base_url: str = "", tag: str = "",
) -> None:
    """Refresh unavailable or still 401s: token is dead. Quarantine the candidate so the caller moves on."""
    _aux._mark_provider_unhealthy(fb_provider or fb_label, base_url=base_url)
    logger.warning("Auxiliary %s%s: fallback candidate %s has a stale/unrefreshable "
                   "credential (%s) — skipping to next fallback", task or "call", tag, fb_label, fb_err)


def _plan_fallback_auth_retry(
    destination: _FallbackDestination,
    rebuild: Callable[[str, Any, Optional[str]], Tuple[_FallbackDestination, Dict[str, Any]]], *,
    async_mode: bool,
    failed_api_key: str = "",
) -> Tuple[str, Optional[Tuple[Any, Dict[str, Any], _FallbackDestination]]]:
    """After an auth error on a fallback candidate: refresh credentials and rebuild the request.
    Returns ``(refresh_provider, retry)``; ``retry`` = ``(client, kwargs, destination)`` or None."""
    fb_provider = _aux._auth_refresh_provider_for_route(destination.provider, destination.base_url)
    refresh_kwargs = {"failed_api_key": failed_api_key} if fb_provider == "anthropic" else {}
    if fb_provider not in {"auto", "", None} and _aux._refresh_provider_credentials(fb_provider, **refresh_kwargs):
        retry_client, retry_model = _aux._get_cached_client(
            fb_provider, destination.model, **({"async_mode": True} if async_mode else {}),
            base_url=destination.base_url or None, api_mode=destination.api_mode,
        )
        if retry_client is not None:
            retry_destination, retry_kwargs = rebuild(fb_provider, retry_client, retry_model)
            return fb_provider, (retry_client, retry_kwargs, retry_destination)
    return fb_provider, None


def _call_fallback_candidate_sync(
    fb_client: Any, fb_model: Optional[str], fb_label: str, *, task: Optional[str], messages: list,
    temperature: Optional[float], max_tokens: Optional[int], tools: Optional[list],
    effective_timeout: float, effective_extra_body: dict, reasoning_config: Optional[dict],
) -> Optional[Any]:
    """Call one fallback candidate with stale-credential recovery: on an auth error refresh its
    credentials and retry once with a rebuilt client; if that also auth-fails, quarantine the
    provider and return None so the caller moves on. Non-auth errors raise.

    ``effective_timeout`` is the task-level deadline; a configured-chain candidate with its own ``timeout``
    entry gets that instead, so a fallback tuned differently from the primary is allowed its own budget
    (#62452).
    """
    destination, fb_kwargs, rebuild = _plan_fallback_candidate(
        fb_client, fb_model, fb_label, task=task, effective_timeout=effective_timeout,
        apply_fast_lane=True, messages=messages, tools=tools, temperature=temperature,
        max_tokens=max_tokens, effective_extra_body=effective_extra_body,
        reasoning_config=reasoning_config,
    )

    def _send(client: Any, request_kwargs: Dict[str, Any], dest: _FallbackDestination) -> Any:
        return _aux._validate_llm_response(
            _aux._relay_sync_completion(
                client, request_kwargs, provider=dest.provider, api_mode=dest.api_mode,
                create=lambda request: _aux._create_with_progress(
                    client, request, task,
                    force_stream=_aux._provider_requires_stream(dest.provider, dest.base_url),
                ),
            ),
            task,
        )
    try:
        return _send(fb_client, fb_kwargs, destination)
    except Exception as fb_err:
        if not _aux._is_auth_error(fb_err):
            raise
        fb_provider, retry = _plan_fallback_auth_retry(
            destination, rebuild, async_mode=False, failed_api_key=getattr(fb_client, "api_key", ""))
        failed_destination = destination
        if retry is not None:
            failed_destination = retry[2]
            try:
                return _send(*retry)
            except Exception as retry_err:
                if not _aux._is_auth_error(retry_err):
                    raise
        _quarantine_fallback_candidate(
            task, fb_label, fb_provider, fb_err, base_url=failed_destination.base_url,
        )
        return None


async def _call_fallback_candidate_async(
    fb_client: Any, fb_model: Optional[str], fb_label: str, *, task: Optional[str], messages: list,
    temperature: Optional[float], max_tokens: Optional[int], tools: Optional[list],
    effective_timeout: float, effective_extra_body: dict, reasoning_config: Optional[dict],
) -> Optional[Any]:
    """Async mirror of :func:`_call_fallback_candidate_sync` (no fast-lane cap on this wire)."""
    destination, fb_kwargs, rebuild = _plan_fallback_candidate(
        fb_client, fb_model, fb_label, task=task, effective_timeout=effective_timeout,
        apply_fast_lane=False, messages=messages, tools=tools, temperature=temperature,
        max_tokens=max_tokens, effective_extra_body=effective_extra_body,
        reasoning_config=reasoning_config,
    )

    async def _send(client: Any, request_kwargs: Dict[str, Any], dest: _FallbackDestination) -> Any:
        return _aux._validate_llm_response(
            await _aux._relay_async_completion(client, request_kwargs, provider=dest.provider, api_mode=dest.api_mode),
            task,
        )
    try:
        return await _send(fb_client, fb_kwargs, destination)
    except Exception as fb_err:
        if not _aux._is_auth_error(fb_err):
            raise
        fb_provider, retry = _plan_fallback_auth_retry(
            destination, rebuild, async_mode=True, failed_api_key=getattr(fb_client, "api_key", ""))
        failed_destination = destination
        if retry is not None:
            failed_destination = retry[2]
            try:
                return await _send(*retry)
            except Exception as retry_err:
                if not _aux._is_auth_error(retry_err):
                    raise
        _quarantine_fallback_candidate(
            task, fb_label, fb_provider, fb_err,
            base_url=failed_destination.base_url, tag=" (async)",
        )
        return None


def _try_payment_fallback(
    failed_provider: str, task: str = None, reason: str = "payment error", *,
    failed_base_url: str = "", failure_scope: Any = None, main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Try the auto-detection chain after a payment/credit or connection error, skipping the failed
    provider (and the main-provider path when it maps to the same backend). Returns (client, model, label) or (None, None, "")."""
    skip = failed_provider.lower().strip()
    # The SESSION's provider decides whether discovery is allowed: a live `/model xai-oauth` session
    # over a persisted ``provider: auto`` is a selection, so the disk value alone is not the answer.
    main_provider = _aux._normalize_main_runtime(main_runtime).get("provider") or _aux._read_main_provider()
    if not _discovery_chain_allowed(main_provider, task):
        return None, None, ""
    skip_labels = {skip}
    if main_provider and main_provider.lower() in skip:
        skip_labels.add(main_provider.lower())
    skip_chain_labels = {_aux._normalize_chain_label(s) for s in skip_labels}
    skip_backend = _failed_backend_skip(
        failed_provider, None, failed_base_url=failed_base_url, failure_scope=failure_scope)
    tried = []
    for label, try_fn in _aux._get_provider_chain():
        candidate_base_url = _aux._custom_health_base_url(label)
        if (not failed_base_url and label in skip_chain_labels) or skip_backend(
                label, None, candidate_base_url):
            continue
        if _aux._is_provider_unhealthy(label, candidate_base_url):
            _aux._log_skip_unhealthy(label, task, base_url=candidate_base_url)
            tried.append(f"{label} (unhealthy)")
            continue
        client, model = try_fn()
        if client is not None:
            logger.info("Auxiliary %s: %s on %s — falling back to %s (%s)",
                        task or "call", reason, failed_provider, label, model or "default")
            return client, model, label
        tried.append(label)
    logger.warning("Auxiliary %s: %s on %s and no fallback available (tried: %s)",
                   task or "call", reason, failed_provider, ", ".join(tried))
    return None, None, ""


def _failed_backend_skip(
    failed_provider: str, failed_model: Optional[str], *, failed_base_url: str = "",
    failure_scope: Any = None,
) -> Callable[..., bool]:
    """Predicate ``skip(provider, model, base_url="")`` → True when a candidate must be skipped for the failed
    route. Scope: ``failed_model`` → model-scoped (only that deployment; timeout/connection/rate-limit);
    None → credential-wide (whole provider; auth/payment)."""
    from agent.backend_identity import BackendIdentity, FailureScope, should_skip_candidate
    skip_model = (failed_model or "").strip().lower() or None
    failed_ident = BackendIdentity.build(
        provider=failed_provider, model=skip_model, base_url=failed_base_url)
    failure_scope = failure_scope or (FailureScope.MODEL if skip_model else FailureScope.CREDENTIAL)

    def _skip(provider: str, model: Optional[str], base_url: str = "") -> bool:
        return should_skip_candidate(
            BackendIdentity.build(provider=provider, model=model, base_url=base_url), failed_ident, failure_scope,
        )
    return _skip


def _try_main_agent_model_fallback(
    failed_provider: str, task: str = None, reason: str = "error",
    failed_model: Optional[str] = None, failed_base_url: str = "", failure_scope: Any = None,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Last-resort fallback to the main agent provider + model after the configured chain is exhausted.
    ``failed_model`` scoping per ``_failed_backend_skip``; same-URL custom endpoints serve many models,
    so a hung aux model says nothing about the main model's health. Returns (client, model, label) or (None, None, "")."""
    main_provider = (_aux._read_main_provider() or "").strip()
    main_model = (_aux._read_main_model() or "").strip()
    if main_provider.lower() == "moa":
        # MoA virtual provider: fall back to the preset's aggregator (the acting model).
        _agg_provider, _agg_model = _aux._resolve_moa_aggregator(main_model)
        if not _agg_provider or not _agg_model:
            return None, None, ""
        main_provider, main_model = _agg_provider, _agg_model
    if not main_provider or not main_model or main_provider.lower() in {"auto", ""}:
        return None, None, ""
    main_base_url = _aux._custom_health_base_url(main_provider)
    if _failed_backend_skip(
            failed_provider, failed_model, failed_base_url=failed_base_url,
            failure_scope=failure_scope)(main_provider, main_model, main_base_url):
        return None, None, ""
    if _aux._is_provider_unhealthy(main_provider, main_base_url):
        _aux._log_skip_unhealthy(main_provider, task, base_url=main_base_url)
        return None, None, ""
    try:
        client, resolved_model = _aux.resolve_provider_client(provider=main_provider, model=main_model)
    except Exception:
        client, resolved_model = None, None
    if client is None:
        return None, None, ""
    label = f"main-agent({main_provider})"
    logger.info("Auxiliary %s: %s on %s — falling back to main agent model %s (%s)",
                task or "call", reason, failed_provider, label, resolved_model or main_model)
    return client, resolved_model or main_model, label


# Context-window screening for runtime fallback chains: the startup feasibility check filters
# too-small aux models; runtime chains must too, or compression stops at a reachable-but-too-small
# candidate. ``None`` (unknown) passes through.

# ── Context-window screening for runtime fallback chains (issue #52392) ── When the runtime auxiliary
# fallback chain selects a candidate that is reachable but has a context window smaller than the compression
# task requires, the call errors out instead of continuing to the next, viable candidate. The startup
# feasibility check in ``agent.conversation_compression.check_compression_model_feasibility`` already
# filters too-small auxiliary models at startup, but the runtime fallback chain
# (``_try_configured_fallback_chain`` and ``_try_main_fallback_chain``) does not apply the same filter, so
# compression can stop at the first alive door even if the room behind it is too small. The helpers below
# screen each candidate by its effective context window before it is returned. ``None`` results from
# ``get_model_context_length`` are passed through (we cannot prove a model is too small, so we do not block
# it). This preserves the existing fallback surface for unrecognised/custom models while closing the gap on
# the well-known ones.
def _task_minimum_context_length(task: Optional[str]) -> Optional[int]:
    """Minimum context length for an auxiliary task; None = no floor (only ``compression`` has one)."""
    return _aux.MINIMUM_CONTEXT_LENGTH if task == "compression" else None


def _candidate_context_window(provider: str, model: str, base_url: str = "", api_key: str = "") -> Optional[int]:
    """Best-effort context window for a fallback candidate; ``None`` = unknown (never raises; callers pass it through)."""
    if not model:
        return None
    try:
        ctx = _aux.get_model_context_length(model, base_url=base_url, api_key=api_key, provider=provider)
    except Exception as exc:
        logger.debug("Auxiliary fallback: could not resolve context window for %s/%s: %s", provider, model, exc)
        return None
    return ctx if isinstance(ctx, int) and ctx > 0 else None


def _context_too_small(
    entry: Dict[str, Any], provider: str, model: str, min_ctx: Optional[int], *,
    task: Optional[str], label: str, name_model: bool = False,
) -> Optional[str]:
    """Screen one fallback candidate by context window; returns the ``tried`` note when it is too small."""
    if min_ctx is None:
        return None
    fb_ctx = _candidate_context_window(
        provider, model, base_url=str(entry.get("base_url") or ""), api_key=_fallback_entry_api_key(entry) or "")
    if fb_ctx is None or fb_ctx >= min_ctx:
        return None
    if name_model:
        logger.info("Auxiliary %s: skipping %s (%s context=%d < min=%d), continuing chain",
                    task, label, model, fb_ctx, min_ctx)
    else:
        logger.info("Auxiliary %s: skipping %s (context=%d < min=%d), continuing chain",
                    task or "call", label, fb_ctx, min_ctx)
    return f"{label} (context too small: {fb_ctx}<{min_ctx})"


def _try_configured_fallback_chain(
    task: str, failed_provider: str, reason: str = "error", failed_model: Optional[str] = None, *,
    failed_base_url: str = "", failure_scope: Any = None,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Try auxiliary.<task>.fallback_chain entries in order (each needs ``provider``; model/base_url/api_key optional).
    ``failed_model`` scoping per ``_failed_backend_skip`` (sibling models on the same provider still
    run after a model-scoped failure). Returns (client, model, provider_label) or (None, None, "")."""
    if not task:
        return None, None, ""
    chain = _aux._get_auxiliary_task_config(task).get("fallback_chain")
    if not chain or not isinstance(chain, list):
        return None, None, ""
    skip = _failed_backend_skip(
        failed_provider, failed_model, failed_base_url=failed_base_url, failure_scope=failure_scope)
    tried = []
    min_ctx = _task_minimum_context_length(task)
    for i, entry in enumerate(chain):
        if not isinstance(entry, dict):
            continue
        fb_provider = str(entry.get("provider", "")).strip()
        if not fb_provider:
            continue
        fb_model_raw = str(entry.get("model", "")).strip()
        fb_base_url = _aux._custom_health_base_url(fb_provider, entry.get("base_url"))
        if skip(fb_provider, fb_model_raw, fb_base_url):
            continue
        if _aux._is_provider_unhealthy(fb_provider, fb_base_url):
            _aux._log_skip_unhealthy(fb_provider, task, base_url=fb_base_url)
            tried.append(f"fallback_chain[{i}]({fb_provider}) (unhealthy)")
            continue
        fb_model = fb_model_raw or None
        label = f"fallback_chain[{i}]({fb_provider})"
        try:
            fb_client, resolved_model = _aux._resolve_fallback_entry(entry)
        except Exception:
            fb_client, resolved_model = None, None
        if fb_client is not None:
            too_small = _context_too_small(
                entry, fb_provider, resolved_model, min_ctx, task=task, label=label, name_model=True,
            ) if resolved_model else None
            if too_small:
                tried.append(too_small)
                continue
            logger.info("Auxiliary %s: %s on %s — configured fallback to %s (%s)",
                        task, reason, failed_provider, label, resolved_model or fb_model or "default")
            return fb_client, resolved_model or fb_model, label
        tried.append(label)
    if tried:
        logger.debug("Auxiliary %s: configured fallback_chain exhausted (tried: %s)", task, ", ".join(tried))
    return None, None, ""


def _try_configured_fallback_for_unavailable_client(
    task: Optional[str], failed_provider: str
) -> Tuple[Optional[Any], Optional[str], str]:
    """Task fallback_chain when an explicit aux provider cannot build a client (no key/OAuth/pool creds);
    stops at the per-task chain — the main-agent model stays the runtime last resort."""
    explicit = (failed_provider or "").strip().lower()
    if not task or not explicit or explicit in {"auto"}:
        return None, None, ""
    return _aux._try_configured_fallback_chain(task, explicit, reason="provider unavailable")


def _fallback_entry_api_key(entry: Dict[str, Any]) -> Optional[str]:
    """Resolve inline or env-backed API key via the secret-scope-aware resolver (no raw os.getenv under multiplexing)."""
    from hermes_cli.fallback_config import resolve_entry_api_key
    return resolve_entry_api_key(entry)


def _resolve_fallback_entry(entry: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve one fallback entry through the central provider router."""
    provider = str(entry.get("provider") or "").strip()
    model = str(entry.get("model") or "").strip() or None
    if not provider or not model:
        return None, None
    client, resolved_model = _aux.resolve_provider_client(
        provider, model=model, explicit_base_url=str(entry.get("base_url") or "").strip() or None,
        explicit_api_key=_fallback_entry_api_key(entry),
        api_mode=str(entry.get("api_mode") or entry.get("transport") or "").strip() or None,
    )
    if client is not None:
        with contextlib.suppress(Exception):
            client._hermes_fallback_destination = _fallback_destination_from_entry(entry, client, resolved_model)
    return client, resolved_model


def _try_main_fallback_chain(
    task: Optional[str], failed_provider: str = "", reason: str = "error", *,
    failed_model: Optional[str] = None, failed_base_url: str = "", failure_scope: Any = None,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Top-level main-agent fallback chain for a ``provider: auto`` auxiliary call: auto tasks honour the
    user's main fallback policy before the built-in discovery chain; read via ``get_fallback_chain`` so
    ``fallback_providers`` and legacy ``fallback_model`` keep the main agent's order."""
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_cli.fallback_config import get_fallback_chain
        chain = get_fallback_chain(load_config_readonly())
    except Exception as exc:
        logger.debug("Auxiliary %s: could not load main fallback chain: %s", task or "call", exc)
        return None, None, ""
    if not chain:
        return None, None, ""
    skip = _failed_backend_skip(
        failed_provider, failed_model, failed_base_url=failed_base_url, failure_scope=failure_scope)
    tried: List[str] = []
    min_ctx = _task_minimum_context_length(task)
    for i, entry in enumerate(chain):
        if not isinstance(entry, dict):
            continue
        fb_provider = str(entry.get("provider") or "").strip()
        fb_model = str(entry.get("model") or "").strip()
        if not fb_provider or not fb_model:
            continue
        fb_norm = fb_provider.lower()
        label = f"fallback_providers[{i}]({fb_provider})"
        fb_base_url = _aux._custom_health_base_url(fb_provider, entry.get("base_url"))
        if fb_norm == "auto" or skip(fb_provider, fb_model, fb_base_url):
            tried.append(f"{label} (skipped)")
            continue
        if _aux._is_provider_unhealthy(fb_norm, fb_base_url):
            _aux._log_skip_unhealthy(fb_norm, task, base_url=fb_base_url)
            tried.append(f"{label} (unhealthy)")
            continue
        try:
            fb_client, resolved_model = _aux._resolve_fallback_entry(entry)
        except Exception as exc:
            logger.debug("Auxiliary %s: main fallback %s failed to resolve: %s", task or "call", label, exc)
            fb_client, resolved_model = None, None
        if fb_client is not None:
            too_small = _context_too_small(
                entry, fb_provider, resolved_model or fb_model, min_ctx, task=task, label=label,
            )
            if too_small:
                tried.append(too_small)
                continue
            logger.info("Auxiliary %s: %s on %s — main fallback chain to %s (%s)",
                        task or "call", reason, failed_provider or "auto", label, resolved_model or fb_model)
            return fb_client, resolved_model or fb_model, fb_provider
        tried.append(label)
    if tried:
        logger.debug("Auxiliary %s: main fallback chain exhausted (tried: %s)", task or "call", ", ".join(tried))
    return None, None, ""


def _main_route_target(runtime: Dict[str, Any], task: Optional[str]) -> Tuple[str, str, str, Any, str]:
    """Step-1 target: (provider, model, base_url, api_key, api_mode) of the main runtime, after the
    fast-model opt-in and the MoA aggregator substitution."""
    main_provider = str(runtime.get("provider", "") or _aux._read_main_provider() or "")
    main_model = str(runtime.get("model") or _aux._read_main_model() or "")
    runtime_base_url = str(runtime.get("base_url") or "")
    runtime_api_key = runtime.get("api_key", "")
    runtime_api_mode = str(runtime.get("api_mode") or "")
    # Latency-critical tasks (titling only) opt in to the provider's fast model. Opt-in only:
    # every settings surface defines "auto" as the main model.
    if _aux._task_prefers_fast_model(task) and main_provider and main_provider not in {"auto", ""}:
        fast_model = _aux._get_aux_model_for_provider(main_provider, prefer_fast=True)
        if fast_model and fast_model != main_model:
            logger.debug("Auxiliary task %s: preferring fast model %s over main model %s",
                         task, fast_model, main_model)
            main_model = fast_model
    # MoA virtual provider: the preset name is not a wire model; run aux on the aggregator and drop
    # the facade's "moa://local" base_url / placeholder key so it uses its own credentials.
    if main_provider == "moa":
        _agg_provider, _agg_model = _aux._resolve_moa_aggregator(main_model)
        if _agg_provider and _agg_model:
            main_provider, main_model = _agg_provider, _agg_model
            runtime_base_url = runtime_api_key = runtime_api_mode = ""
    return main_provider, main_model, runtime_base_url, runtime_api_key, runtime_api_mode


def _try_main_provider_route(
    main_provider: str, main_model: str, runtime_base_url: str, runtime_api_key: Any, runtime_api_mode: str,
) -> Optional[Tuple[Any, str, str]]:
    """Step 1: route aux onto the main provider + main model; None if unusable."""
    if not (main_provider and main_model and main_provider not in {"auto", ""}):
        return None
    resolved_provider = main_provider
    explicit_base_url = runtime_base_url or None
    health_base_url = _aux._custom_health_base_url(main_provider, explicit_base_url)
    explicit_api_key = None
    if runtime_base_url and main_provider == "custom":
        # Anonymous custom endpoint — pass through explicit base_url + api_key.
        explicit_api_key = runtime_api_key or None
    elif main_provider.startswith("custom:"):
        # Named custom provider (custom_providers / providers dict entry).
        _has_named_entry = False
        with contextlib.suppress(ImportError):
            from hermes_cli.runtime_provider import _get_named_custom_provider
            _has_named_entry = _get_named_custom_provider(main_provider) is not None
        if _has_named_entry:
            # KEEP the full ``custom:<name>`` so the named arm honours the entry's api_mode
            # (collapsing to "custom" strips /anthropic → 404s). base_url/api_key come from the entry.
            explicit_base_url = None
        elif runtime_base_url:
            # Config-less named custom provider (live runtime only): anonymous custom arm + runtime key.
            # See #34777.
            resolved_provider = "custom"
            explicit_api_key = runtime_api_key or None
        elif runtime_api_key:
            explicit_api_key = runtime_api_key
    elif runtime_api_key:
        # Pin aux to the main session's working key, not a re-selected (maybe exhausted) pool key.
        explicit_api_key = runtime_api_key
    # Skip if the main provider was recently 402'd (unhealthy TTL bounds the bypass).
    main_chain_label = _aux._normalize_chain_label(resolved_provider)
    if main_chain_label and _aux._is_provider_unhealthy(main_chain_label, health_base_url):
        _aux._log_skip_unhealthy(main_chain_label, base_url=health_base_url)
        return None
    client, resolved = _aux.resolve_provider_client(
        resolved_provider, main_model, explicit_base_url=explicit_base_url,
        explicit_api_key=explicit_api_key, api_mode=runtime_api_mode or None,
    )
    if client is None:
        return None
    logger.info("Auxiliary auto-detect: using main provider %s (%s)", main_provider, resolved or main_model)
    return client, resolved or main_model, resolved_provider


def _discovery_chain_allowed(main_provider: str, task: Optional[str] = None) -> bool:
    """The built-in discovery chain is a convenience for installs with NO selected main provider.
    Once the user picked one, every auxiliary route must be a provider they configured (main,
    ``auxiliary.<task>``, ``fallback_providers``); guessing "whatever else is logged in" bills an
    account they never pointed this session at (xAI OAuth session with a dead token → every
    compression silently charged to a Nous Portal balance)."""
    if (main_provider or "").strip().lower() in {"", "auto"}:
        return True
    logger.warning(
        "Auxiliary %s: main provider %s is unavailable and no fallback_chain / fallback_providers is "
        "configured — refusing to guess another logged-in provider. Re-authenticate (`hermes model`) "
        "or declare a fallback.", task or "call", main_provider)
    return False


def _try_discovery_chain() -> Tuple[Optional[_aux.OpenAI], Optional[str], str]:
    """Step 3: hardcoded aggregator/fallback chain, skipping unhealthy providers."""
    tried = []
    for label, try_fn in _aux._get_provider_chain():
        candidate_base_url = _aux._custom_health_base_url(label)
        if _aux._is_provider_unhealthy(label, candidate_base_url):
            _aux._log_skip_unhealthy(label, base_url=candidate_base_url)
            tried.append(f"{label} (unhealthy)")
            continue
        client, model = try_fn()
        if client is not None:
            if tried:
                logger.info("Auxiliary auto-detect: using %s (%s) — skipped: %s",
                            label, model or "default", ", ".join(tried))
            else:
                logger.info("Auxiliary auto-detect: using %s (%s)", label, model or "default")
            return client, model, label
        tried.append(label)
    logger.warning("Auxiliary auto-detect: no provider available (tried: %s). "
                   "Compression, summarization, and memory flush will not work. "
                   "Set OPENROUTER_API_KEY or configure a local model in config.yaml.", ", ".join(tried))
    return None, None, ""


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
