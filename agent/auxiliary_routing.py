"""Resolving a provider to a client: sync-to-async conversion, Bedrock/Vertex builders, the
per-provider branches behind ``resolve_provider_client``, and vision backend resolution.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


def _effective_provider_for_client(client: Any, fallback: str) -> str:
    """Return the concrete provider selected for an auto-routed client."""
    effective_provider = getattr(client, "_hermes_aux_effective_provider", "")
    if isinstance(effective_provider, str) and effective_provider:
        return effective_provider
    return str(fallback or "")


# Centralized Provider Router: resolve_provider_client() is the single entry point for building a configured
# client (auth, base URL, headers, API format) from (provider, model). Never read auth env vars ad-hoc.


def _to_async_client(sync_client, model: str, is_vision: bool = False):
    """Sync client → async counterpart, preserving Codex routing (``is_vision`` adds the Copilot vision header)."""
    from openai import AsyncOpenAI
    if isinstance(sync_client, _aux._AuxProbeClientStub):
        return sync_client, model
    if isinstance(sync_client, _aux.CodexAuxiliaryClient):
        return _aux.AsyncCodexAuxiliaryClient(sync_client), model
    if isinstance(sync_client, _aux.AnthropicAuxiliaryClient):
        return _aux.AsyncAnthropicAuxiliaryClient(sync_client), model
    if isinstance(sync_client, _aux.BedrockAuxiliaryClient):
        return _aux.AsyncBedrockAuxiliaryClient(sync_client), model
    with contextlib.suppress(ImportError):
        from agent.gemini_native_adapter import GeminiNativeClient, AsyncGeminiNativeClient
        if isinstance(sync_client, GeminiNativeClient):
            return AsyncGeminiNativeClient(sync_client), model
    # ACP shims (subprocess, not an HTTP pool) are already async-safe and opt out of the wrapper.
    if _aux._client_declares(sync_client, "HERMES_SKIP_ASYNC_WRAP"):
        return sync_client, model
    sync_base_url = str(sync_client.base_url)
    async_kwargs = {"api_key": sync_client.api_key, "base_url": sync_base_url}
    if _aux.base_url_host_matches(sync_base_url, "openrouter.ai"):
        headers = _aux._apply_user_default_headers(_aux.build_or_headers())
    elif _aux._is_official_codex_base_url(sync_base_url):
        headers = _aux._apply_user_default_headers(_aux._codex_cloudflare_headers(sync_client.api_key, base_url=sync_base_url))
    else:
        # Provider for the profile-header fallback is inferred from the hostname.
        try:
            from agent.model_metadata import _infer_provider_from_url
            inferred = _infer_provider_from_url(sync_base_url) or ""
        except Exception:
            inferred = ""
        headers = _aux._endpoint_default_headers(sync_base_url, inferred, is_vision=is_vision, xai=True)
    # Headers are rebuilt from scratch here, so re-apply the OpenCode keyless policy from
    # _create_openai_client: the placeholder must never ship as a bearer (see #110831).
    with contextlib.suppress(Exception):
        from hermes_cli.models import OPENCODE_ZEN_FREE_KEYLESS_PLACEHOLDER, opencode_zen_free_headers
        if sync_client.api_key == OPENCODE_ZEN_FREE_KEYLESS_PLACEHOLDER:
            headers = {**(headers or {}), **opencode_zen_free_headers()}
    if headers:
        async_kwargs["default_headers"] = headers
    _aux._apply_required_codex_headers(async_kwargs, access_token=sync_client.api_key, base_url=sync_base_url)
    async_kwargs = {**_aux._openai_http_client_kwargs(sync_base_url, async_mode=True), **async_kwargs}
    # Hermes owns the auxiliary retry/timeout budget; disable SDK-internal retries.
    # See #54465.
    async_kwargs.setdefault("max_retries", 0)
    return AsyncOpenAI(**async_kwargs), model


def _normalize_resolved_model(model_name: Optional[str], provider: str) -> Optional[str]:
    """Normalize a resolved model for the provider that will receive it."""
    if not model_name:
        return model_name
    try:
        from hermes_cli.model_normalize import normalize_model_for_provider
        return normalize_model_for_provider(model_name, provider)
    except Exception:
        return model_name


def _named_custom_api_key(custom_entry: Dict[str, Any], provider: str, custom_base: str) -> Any:
    """Credential for a named custom provider: inline api_key → key_env → key_cmd → credential pool → placeholder.
    Aux resolves named custom providers here, not via _resolve_named_custom_runtime, so key_cmd must be
    honoured at the same precedence or every aux call 401s."""
    custom_key: Any = (custom_entry.get("api_key") or "").strip()
    custom_key_env = (custom_entry.get("key_env") or custom_entry.get("api_key_env") or "").strip()
    if not custom_key and custom_key_env:
        custom_key = _aux._scoped_key_env(custom_key_env)
    custom_key_cmd = str(custom_entry.get("key_cmd", "") or "").strip()
    if custom_key_cmd:
        from agent.command_token_source import build_command_token_provider
        custom_key = build_command_token_provider(custom_key_cmd, custom_entry.get("name") or provider) or custom_key
    if not custom_key:
        with contextlib.suppress(Exception):
            from agent.credential_pool import custom_provider_pool_key_candidates
            pool_name = custom_entry.get("provider_key") or custom_entry.get("name") or provider
            for pool_key in custom_provider_pool_key_candidates(custom_base, pool_name):
                try:
                    pool = _aux.load_pool(pool_key)
                except Exception:
                    continue
                if not pool.has_credentials():
                    continue
                pool_entry = pool.select()
                if pool_entry is None:
                    continue
                pool_api_key = getattr(pool_entry, "runtime_api_key", None) or getattr(pool_entry, "access_token", "") or ""
                if str(pool_api_key).strip():
                    custom_key = str(pool_api_key).strip()
                    break
    return custom_key or "no-key-required"


def _build_bedrock_client(provider: str, model: Optional[str], *, raw_codex: bool) -> Tuple[Optional[Any], Optional[str]]:
    """AWS Bedrock: Claude → Anthropic Bedrock SDK (prompt caching, thinking); OpenAI models
    (GPT-5.5/5.6) → Bedrock Mantle's OpenAI Responses endpoint; everything else → Converse API."""
    try:
        from agent.bedrock_adapter import (
            has_aws_credentials, is_anthropic_bedrock_model, resolve_bedrock_runtime_region,
            is_openai_bedrock_model, bedrock_openai_base_url, resolve_bedrock_bearer_token,
            configure_bedrock_openai_client_kwargs,
        )
        from agent.anthropic_adapter import build_anthropic_bedrock_client
    except ImportError:
        logger.warning("resolve_provider_client: bedrock requested but boto3, httpx/openai, or anthropic SDK not installed")
        return None, None
    if not has_aws_credentials():
        logger.debug("resolve_provider_client: bedrock requested but no AWS credentials found")
        return None, None
    # Region must match the main runtime's resolution (bedrock.region in config first, then
    # env/profile) so aux calls never leave the primary runtime's configured region.
    # See #53880, #65076.
    region = resolve_bedrock_runtime_region()
    default_model = "anthropic.claude-haiku-4-5-20251001-v1:0"
    final_model = _normalize_resolved_model(model or default_model, provider) or default_model
    if is_openai_bedrock_model(final_model):
        # Module-level lazy ``OpenAI`` proxy on purpose so tests can patch("agent.auxiliary_client.OpenAI").
        client_kwargs: Dict[str, Any] = {
            "api_key": resolve_bedrock_bearer_token() or "aws-sdk",
            "base_url": bedrock_openai_base_url(region),
        }
        configure_bedrock_openai_client_kwargs(client_kwargs)
        client = _aux.OpenAI(**client_kwargs)
        logger.debug("resolve_provider_client: bedrock-openai (%s, %s)", final_model, region)
        return (client if raw_codex else _aux.CodexAuxiliaryClient(client, final_model)), final_model
    base_url = f"https://bedrock-runtime.{region}.amazonaws.com"
    if is_anthropic_bedrock_model(final_model):
        try:
            real_client = build_anthropic_bedrock_client(region)
        except ImportError as exc:
            logger.warning("resolve_provider_client: cannot create Bedrock client: %s", exc)
            return None, None
        client = _aux.AnthropicAuxiliaryClient(real_client, final_model, api_key="aws-sdk", base_url=base_url)
        logger.debug("resolve_provider_client: bedrock anthropic (%s, %s)", final_model, region)
    else:
        client = _aux.BedrockAuxiliaryClient(region, final_model)
        logger.debug("resolve_provider_client: bedrock converse (%s, %s)", final_model, region)
    return client, final_model


def _build_vertex_client(provider: str, model: Optional[str]) -> Tuple[Optional[Any], Optional[str]]:
    """Google Vertex AI: Gemini via the OpenAI-compatible endpoint with an OAuth2 bearer (standard OpenAI client)."""
    try:
        from agent.vertex_adapter import get_vertex_config, has_vertex_credentials
    except ImportError:
        logger.warning("resolve_provider_client: vertex requested but google-auth not installed")
        return None, None
    if not has_vertex_credentials():
        logger.debug("resolve_provider_client: vertex requested but no GCP credentials found")
        return None, None
    token, base_url = get_vertex_config()
    if not token or not base_url:
        logger.warning("resolve_provider_client: vertex requested but could not mint token / resolve project")
        return None, None
    final_model = _normalize_resolved_model(model or "google/gemini-3-flash-preview", provider)
    try:
        # Aliased import: a bare `from openai import OpenAI` would shadow the module-level lazy proxy.
        from openai import OpenAI as _VertexOpenAI
        client = _VertexOpenAI(api_key=token, base_url=base_url)
    except Exception as exc:
        logger.warning("resolve_provider_client: cannot create Vertex client: %s", exc)
        return None, None
    logger.debug("resolve_provider_client: vertex (%s)", final_model)
    return client, final_model


class _ResolveRequest(NamedTuple):
    """Normalized resolve_provider_client() arguments shared by the per-provider branch helpers."""
    provider: str
    original_provider: str
    model: Optional[str]
    async_mode: bool
    raw_codex: bool
    explicit_base_url: Optional[str]
    explicit_api_key: Optional[str]
    api_mode: Optional[str]
    main_runtime: Optional[Dict[str, Any]]
    is_vision: bool
    task: Optional[str]


_ResolveResult = Tuple[Optional[Any], Optional[str]]


def _log_once_debug(seen: set, key: Any, msg: str, *args: Any) -> None:
    """Debug-log ``msg`` the first time ``key`` is seen so per-call retries stay silent."""
    if key not in seen:
        seen.add(key)
        logger.debug(msg, *args)


def _is_actual_auxiliary_route(req: _ResolveRequest, base_url: str) -> bool:
    from hermes_cli.auth import normalize_actual_base_url
    from hermes_cli.providers import is_actual_route
    from hermes_cli.route_identity import normalize_route_base_url

    if is_actual_route(req.provider, base_url):
        return True
    runtime = _aux._normalize_main_runtime(req.main_runtime)
    return bool(
        base_url
        and is_actual_route(runtime.get("provider", ""), runtime.get("base_url", ""))
        and normalize_route_base_url(normalize_actual_base_url(base_url))
        == normalize_route_base_url(
            normalize_actual_base_url(runtime.get("base_url", ""))
        )
    )


def _wrap_transport(req: _ResolveRequest, client_obj: Any, final_model_str: str,
                    base_url_str: str = "", api_key_str: str = ""):
    """Wrap a plain OpenAI client in the right transport adapter; specialized wrappers pass through.
    Codex (Responses API): explicit ``api_mode=codex_responses``, else — with no
    explicit api_mode — api.openai.com + codex model. Anthropic (Messages): ``api_mode=anthropic_messages``,
    any ``/anthropic`` suffix, ``api.kimi.com/coding``, or ``api.anthropic.com``."""
    if _is_actual_auxiliary_route(req, base_url_str):
        client = (
            client_obj._real_client
            if isinstance(client_obj, _aux.CodexAuxiliaryClient)
            else client_obj
        )
        client._hermes_aux_effective_provider = "actual"
        return client
    needs_codex = not (
        isinstance(client_obj, _aux.CodexAuxiliaryClient) or req.raw_codex
    ) and (
        req.api_mode == "codex_responses"
        or (
            not req.api_mode
            and _aux.base_url_hostname(base_url_str) == "api.openai.com"
            and "codex" in (final_model_str or "").lower()
        )
    )
    if needs_codex:
        logger.debug("resolve_provider_client: wrapping client in CodexAuxiliaryClient "
                     "(api_mode=%s, model=%s, base_url=%s)",
                     req.api_mode or "auto-detected", final_model_str, base_url_str[:60] if base_url_str else "")
        return _aux.CodexAuxiliaryClient(client_obj, final_model_str)
    # A profile that declares the Messages wire (commandcode-anthropic) is on it whatever the URL
    # looks like; the same declaration gates ``_reasoning_config`` in _build_call_kwargs.
    api_mode = req.api_mode or _profile_declared_messages_wire(req.provider)
    return _aux._maybe_wrap_anthropic(client_obj, final_model_str, api_key_str, base_url_str, api_mode)


def _profile_declared_messages_wire(provider: str) -> Optional[str]:
    """``"anthropic_messages"`` when the registered profile declares that api_mode, else None."""
    from providers import get_provider_profile
    profile = get_provider_profile(str(provider or "").strip().lower())
    return "anthropic_messages" if profile is not None and profile.api_mode == "anthropic_messages" else None


def _route_client(req: _ResolveRequest, client_obj: Any, final_model_str: Optional[str]) -> _ResolveResult:
    """Return (client, model), converting to the async wrapper when ``req.async_mode``."""
    if req.async_mode:
        return _aux._to_async_client(client_obj, final_model_str, is_vision=req.is_vision)
    return client_obj, final_model_str


def _route_or_warn(req: _ResolveRequest, client: Any, default: Optional[str], unavailable_msg: str, *args: Any) -> _ResolveResult:
    """Route ``client`` on ``req.model or default``; warn and return (None, None) when the provider produced no client."""
    if client is None:
        logger.warning(unavailable_msg, *args)
        return None, None
    return _route_client(req, client, _normalize_resolved_model(req.model or default, req.provider))


def _resolve_auto_branch(req: _ResolveRequest) -> _ResolveResult:
    """Auto: try all providers in priority order; tag the client with the effective provider (survives cache reuse)."""
    client, resolved, effective_provider = _aux._resolve_auto_route(main_runtime=req.main_runtime, task=req.task)
    if client is None:
        return None, None
    model = req.model
    # An OpenRouter-format model override won't work on a non-OpenRouter provider (e.g. local
    # server); drop it for the provider's default.
    if model and "/" in model and resolved and "/" not in resolved:
        logger.debug("Dropping OpenRouter-format model %r for non-OpenRouter "
                     "auxiliary provider (using %r instead)", model, resolved)
        model = None
    routed_client, routed_model = _route_client(req, client, model or resolved)
    if routed_client is not None and effective_provider:
        try:
            setattr(routed_client, "_hermes_aux_effective_provider", effective_provider)
        except (AttributeError, TypeError):
            logger.debug("Auxiliary client %s cannot retain effective provider %s",
                         type(routed_client).__name__, effective_provider)
    return routed_client, routed_model


def _resolve_openrouter_branch(req: _ResolveRequest) -> _ResolveResult:
    """OpenRouter."""
    client, default = _aux._try_openrouter(explicit_api_key=req.explicit_api_key, model=req.model)
    if client is None:
        logger.warning("resolve_provider_client: openrouter requested but %s",
                       _aux._describe_openrouter_unavailable(model=req.model))
        return None, None
    return _route_client(req, client, _normalize_resolved_model(req.model or default, req.provider))


def _resolve_nous_branch(req: _ResolveRequest) -> _ResolveResult:
    """Nous Portal (OAuth)."""
    model = req.model
    # Vision: caller flag, _PROVIDER_VISION_MODELS override, or a known vision id.
    client, default = _aux._try_nous(vision=(req.is_vision or model in _aux._PROVIDER_VISION_MODELS.values()
                                        or (model or "").strip().lower() == "mimo-v2-omni"))
    if client is None:
        logger.warning("resolve_provider_client: nous requested but Nous Portal not configured (run: hermes auth)")
        return None, None
    final_model = _normalize_resolved_model(model or default, req.provider)
    # Dual-wire: anthropic/* → /v1/messages, else /chat/completions. Derive from the catalog id
    # (not a stale api_mode) so aux matches the main agent.
    from hermes_cli.providers import nous_api_mode
    client = _aux._maybe_wrap_anthropic(
        client, final_model, str(getattr(client, "api_key", "") or ""),
        str(getattr(client, "base_url", "") or ""), nous_api_mode(final_model),
    )
    return _route_client(req, client, final_model)


def _resolve_openai_codex_branch(req: _ResolveRequest) -> _ResolveResult:
    """OpenAI Codex (OAuth → Responses API)."""
    model = req.model
    if not model:
        logger.warning("resolve_provider_client: openai-codex requested without a "
                       "model; pass model explicitly (e.g. model.model in config.yaml "
                       "or auxiliary.<task>.model for per-task aux routing).")
        return None, None
    no_token_msg = "resolve_provider_client: openai-codex requested but no Codex OAuth token found (run: hermes model)"
    if req.raw_codex:
        # Raw OpenAI client for callers needing responses.stream() (main agent loop).
        codex_token = _aux._read_codex_access_token()
        if not codex_token:
            logger.warning(no_token_msg)
            return None, None
        base_url = _aux._codex_base_url_override() or _aux._CODEX_AUX_BASE_URL
        raw_client = _aux._create_openai_client(api_key=codex_token, base_url=base_url,
                                           default_headers=_aux._codex_cloudflare_headers(codex_token, base_url=base_url))
        return raw_client, _normalize_resolved_model(model, req.provider)
    client, default = _aux._build_codex_client(model)
    return _route_or_warn(req, client, default, no_token_msg)


def _resolve_xai_oauth_branch(req: _ResolveRequest) -> _ResolveResult:
    """xAI Grok OAuth (device code → Responses API). Without this branch xai-oauth falls to the generic
    oauth_external arm, returns (None, None), and silently re-routes every aux task to the Step-2 fallback."""
    client, default = _aux._build_xai_oauth_aux_client(req.model)
    return _route_or_warn(req, client, default,
                          "resolve_provider_client: xai-oauth requested but no xAI "
                          "OAuth token found (run: hermes model -> xAI Grok OAuth — SuperGrok / Premium+)")


def _resolve_custom_branch(req: _ResolveRequest) -> _ResolveResult:
    """Custom endpoint (OPENAI_BASE_URL + OPENAI_API_KEY)."""
    provider, model, main_runtime = req.provider, req.model, req.main_runtime
    # wrap_base: base for the Anthropic-wrap decision. anthropic_messages must keep the raw
    # /anthropic base while the plain OpenAI client uses the /v1-rewritten custom_base (never
    # /anthropic/chat/completions). Empty means "use custom_base".
    custom_base = custom_key = wrap_base = ""
    if req.explicit_base_url:
        custom_base = _aux._to_openai_base_url(req.explicit_base_url).strip()
        if req.api_mode == "anthropic_messages":
            wrap_base = (req.explicit_base_url or "").strip().rstrip("/")
        custom_key = (
            (req.explicit_api_key or "").strip()
            or _aux._scoped_key_env("OPENAI_API_KEY")
            or _aux._read_main_api_key_if_same_host(custom_base)
            or "no-key-required"  # local servers don't need auth
        )
        if not custom_base:
            logger.warning("resolve_provider_client: explicit custom endpoint requested but base_url is empty")
            return None, None
    elif main_runtime:
        # Reuse main_runtime's concrete base_url + api_key for a named custom provider;
        # re-resolving from bare "custom" loses the name and lands on the wrong provider.
        # Re-resolution loses the provider name and falls back to OpenRouter or a wrong API-key provider —
        # the main agent already solved this, we just need to reuse its answer. (#45472)
        _main_base = str(main_runtime.get("base_url") or "").strip().rstrip("/")
        _main_key = str(main_runtime.get("api_key") or "").strip()
        if _main_base and _main_key:
            custom_base, custom_key = _main_base, _main_key
    if custom_base and custom_key:
        if _is_actual_auxiliary_route(req, custom_base):
            from hermes_cli.auth import normalize_actual_base_url
            custom_base = normalize_actual_base_url(custom_base)
        final_model = _normalize_resolved_model(
            model or (main_runtime.get("model") if main_runtime else None) or "gpt-4o-mini", provider,
        )
        extra = {}
        _clean_base, _dq = _aux._extract_url_query_params(custom_base)
        if _dq:
            extra["default_query"] = _dq
        _custom_headers = _aux._endpoint_default_headers(custom_base, provider, is_vision=req.is_vision)
        if _custom_headers:
            extra["default_headers"] = _custom_headers
        client = _aux._create_openai_client(api_key=custom_key, base_url=_clean_base, **extra)
        client = _wrap_transport(req, client, final_model, wrap_base or custom_base, custom_key)
        return _route_client(req, client, final_model)
    # Try custom first, then API-key providers (Codex excluded here:
    # falling through to Codex with no model is a stale-constant trap).
    for try_fn in (_aux._try_custom_endpoint, _aux._resolve_api_key_provider):
        client, default = try_fn()
        if client is not None:
            final_model = _normalize_resolved_model(model or default, provider)
            # ``client.api_key`` may be a callable (Azure Entra bearer provider);
            # wrapping decisions only need base_url + api_mode.
            _raw_ckey = getattr(client, "api_key", "")
            _ckey = "" if (callable(_raw_ckey) and not isinstance(_raw_ckey, str)) else str(_raw_ckey or "")
            client = _wrap_transport(req, client, final_model, str(getattr(client, "base_url", "") or ""), _ckey)
            return _route_client(req, client, final_model)
    logger.warning("resolve_provider_client: custom/main requested but no endpoint credentials found")
    return None, None


def _named_custom_openai_wire_client(custom_base: str, custom_key: Any):
    """Plain OpenAI client on the /v1 equivalent of a named custom entry's base URL."""
    _clean_base, _dq = _aux._extract_url_query_params(_aux._to_openai_base_url(custom_base))
    _extra = {"default_query": _dq} if _dq else {}
    _headers = _aux._apply_user_default_headers(None)
    if _headers:
        _extra["default_headers"] = _headers
    return _aux._create_openai_client(api_key=custom_key, base_url=_clean_base, **_extra)


def _resolve_named_custom_branch(req: _ResolveRequest) -> Optional[_ResolveResult]:
    """Named custom provider (config.yaml providers dict / custom_providers list); None if no entry matches."""
    from hermes_cli.runtime_provider import _get_named_custom_provider
    provider = req.provider
    # If the raw name is an alias (``kimi`` → ``kimi-coding``) and a custom_providers entry exists
    # under it, the custom entry wins over alias rewriting. Only for aliases, so entries matching a
    # canonical name (e.g. ``nous``) still defer to the built-in.
    custom_entry = None
    if req.original_provider and req.original_provider != provider:
        custom_entry = _get_named_custom_provider(req.original_provider)
    if custom_entry is None:
        custom_entry = _get_named_custom_provider(provider)
    if not custom_entry:
        return None
    # A per-task/explicit base_url or api_key composes OVER the named entry's defaults: the entry supplies
    # whatever the caller left blank, never replaces what the caller set (compression prompts carry
    # conversation history, so a silently swapped destination is a data-routing bug, not a nuisance).
    custom_base = (req.explicit_base_url or custom_entry.get("base_url") or "").strip()
    custom_key = (req.explicit_api_key or "").strip() or _named_custom_api_key(custom_entry, provider, custom_base)
    if custom_key == "no-key-required":
        logger.warning("resolve_provider_client: named custom provider %r has no resolvable "
                       "api_key — request will be sent with placeholder no-key-required "
                       "and will 401 on auth-required endpoints", custom_entry.get("name") or provider)
    # Actual's wire protocol takes precedence over persisted task/provider modes.
    entry_api_mode = (req.api_mode or custom_entry.get("api_mode") or "").strip()
    if _is_actual_auxiliary_route(req, custom_base):
        from hermes_cli.auth import normalize_actual_base_url
        custom_base = normalize_actual_base_url(custom_base)
        entry_api_mode = "chat_completions"
    if not custom_base:
        logger.warning("resolve_provider_client: named custom provider %r has no base_url", provider)
        return None, None
    final_model = _normalize_resolved_model(
        req.model
        or custom_entry.get("model")
        or (req.main_runtime.get("model") if req.main_runtime else None)
        or _aux._read_main_model_for_aux()
        or "gpt-4o-mini",
        provider,
    )
    logger.debug("resolve_provider_client: named custom provider %r (%s, api_mode=%s)",
                 provider, final_model, entry_api_mode or "chat_completions")
    # anthropic_messages: route via AnthropicAuxiliaryClient (mirrors _try_custom_endpoint);
    # the Anthropic SDK sees the original (un-rewritten) URL.
    # Mirrors the anonymous-custom branch in _try_custom_endpoint(). See #15033.
    if entry_api_mode == "anthropic_messages":
        try:
            from agent.anthropic_adapter import build_anthropic_client
            real_client = build_anthropic_client(custom_key, custom_base)
        except ImportError:
            logger.warning("Named custom provider %r declares api_mode=anthropic_messages but the anthropic SDK "
                           "is not installed — falling back to OpenAI-wire.", provider)
            return _route_client(req, _named_custom_openai_wire_client(custom_base, custom_key), final_model)
        return _route_client(
            req, _aux.AnthropicAuxiliaryClient(real_client, final_model, custom_key, custom_base, is_oauth=False), final_model)
    client = _named_custom_openai_wire_client(custom_base, custom_key)
    # codex_responses, or auto-detect via _wrap_transport (which reads the task-level api_mode).
    if entry_api_mode == "codex_responses":
        client = _aux.CodexAuxiliaryClient(client, final_model)
    else:
        client = _wrap_transport(req, client, final_model, custom_base, custom_key)
    return _route_client(req, client, final_model)


def _resolve_azure_foundry_branch(req: _ResolveRequest) -> _ResolveResult:
    """Azure Foundry via the runtime resolver: the generic PROVIDER_REGISTRY path only knows the static
    AZURE_FOUNDRY_API_KEY env var, missing ``auth_mode: entra_id`` (callable bearer) and config base_url overrides."""
    client, default_model = _aux._try_azure_foundry(model=req.model, explicit_api_key=req.explicit_api_key,
                                               explicit_base_url=req.explicit_base_url, api_mode=req.api_mode)
    return _route_or_warn(req, client, default_model,
                          "resolve_provider_client: azure-foundry requested but "
                          "runtime resolution failed (run: hermes doctor for diagnostics)")


def _resolve_api_key_branch(req: _ResolveRequest, pconfig: Any, resolve_creds: Callable) -> _ResolveResult:
    """PROVIDER_REGISTRY ``api_key`` providers (Anthropic via its own resolver), honouring explicit overrides."""
    provider = req.provider
    if provider == "anthropic":
        client, default_model = _aux._try_anthropic(explicit_api_key=req.explicit_api_key)
        return _route_or_warn(req, client, default_model,
                              "resolve_provider_client: anthropic requested but no Anthropic credentials found")
    creds = resolve_creds(provider)
    api_key = str(creds.get("api_key", "")).strip()
    # Explicit api_key override (fallback_model / custom_providers entry) lets callers
    # authenticate where no built-in credential is registered for this alias.
    if req.explicit_api_key:
        api_key = req.explicit_api_key.strip() or api_key
    raw_base_url = str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
    if req.explicit_base_url:
        raw_base_url = req.explicit_base_url.strip().rstrip("/")
    # OpenCode Zen free tier (*-free slugs) is served anonymously on the Zen relay only;
    # any bearer (even a Go subscription key) is rejected, so route keyless regardless of creds.
    try:
        from hermes_cli.models import opencode_zen_free_runtime as _oc_free_rt
        _free_rt = _oc_free_rt(provider, req.model)
    except Exception:
        _free_rt = None
    if _free_rt is not None:
        api_key = _free_rt["api_key"]
        raw_base_url = str(_free_rt["base_url"]).rstrip("/")
    if provider == "actual":
        with contextlib.suppress(Exception):
            from hermes_cli.auth import (
                ACTUAL_LOCAL_NOAUTH_PLACEHOLDER, is_actual_local_base_url, normalize_actual_base_url
            )
            raw_base_url = normalize_actual_base_url(raw_base_url)
            if not api_key and is_actual_local_base_url(raw_base_url):
                api_key = ACTUAL_LOCAL_NOAUTH_PLACEHOLDER
    if not api_key:
        tried_sources = list(pconfig.api_key_env_vars) + (["gh auth token"] if provider == "copilot" else [])
        logger.debug("resolve_provider_client: provider %s has no API key configured (tried: %s)",
                     provider, ", ".join(tried_sources))
        return None, None
    base_url = _aux._to_openai_base_url(raw_base_url)
    # Explicit base_url override: a fallback_model/custom_providers entry pointing a built-in name elsewhere.
    if req.explicit_base_url and provider != "actual":
        base_url = _aux._to_openai_base_url(req.explicit_base_url.strip().rstrip("/"))
    final_model = _normalize_resolved_model(req.model or _aux._get_aux_model_for_provider(provider), provider)
    if provider == "gemini":
        from agent.gemini_native_adapter import GeminiNativeClient, is_native_gemini_base_url
        if is_native_gemini_base_url(base_url):
            client = GeminiNativeClient(api_key=api_key, base_url=base_url)
            logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
            return _route_client(req, client, final_model)
    headers = _aux._endpoint_default_headers(base_url, provider, is_vision=req.is_vision, xai=True)
    client = _aux._create_openai_client(api_key=api_key, base_url=base_url, **({"default_headers": headers} if headers else {}))
    # Copilot GPT-5+ models (except gpt-5-mini) are only reachable via the Responses API;
    # wrap so call_llm() transparently routes through responses.stream().
    if provider == "copilot" and final_model and not req.raw_codex:
        with contextlib.suppress(ImportError):
            from hermes_cli.models import _should_use_copilot_responses_api
            if _should_use_copilot_responses_api(final_model):
                logger.debug("resolve_provider_client: copilot model %s needs "
                             "Responses API — wrapping with CodexAuxiliaryClient", final_model)
                client = _aux.CodexAuxiliaryClient(client, final_model)
    # api_mode handling for any API-key provider (direct OpenAI + codex model) and Anthropic-wire
    # endpoints (api.kimi.com/coding, /anthropic gateways) without per-provider branches.
    client = _wrap_transport(req, client, final_model, raw_base_url, api_key)
    logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
    return _route_client(req, client, final_model)


def _resolve_external_process_branch(req: _ResolveRequest, creds: Dict[str, Any]) -> _ResolveResult:
    """PROVIDER_REGISTRY ``external_process`` providers, served via their registered profile."""
    provider = req.provider
    final_model = _normalize_resolved_model(
        req.model or (req.main_runtime.get("model") if req.main_runtime else None) or _aux._read_main_model_for_aux(),
        provider,
    )
    # Keyed on the registered profile, not a provider name, so an out-of-tree ACP provider reaches
    # the auxiliary path (compression, vision, background review) exactly like the in-tree one.
    try:
        from providers import get_provider_profile as _get_provider_profile
        _extproc_profile = _get_provider_profile(provider)
    except Exception:
        _extproc_profile = None
    if _extproc_profile is not None:
        api_key = str(creds.get("api_key", "")).strip()
        base_url = str(creds.get("base_url", "")).strip()
        if not final_model:
            logger.warning("resolve_provider_client: %s requested but no model was provided or configured", provider)
            return None, None
        if not api_key or not base_url:
            logger.warning("resolve_provider_client: %s requested but external process credentials are incomplete", provider)
            return None, None
        try:
            client = _extproc_profile.create_client(
                api_key=api_key, base_url=base_url,
                command=str(creds.get("command", "")).strip() or None, args=list(creds.get("args") or []))
        except Exception:
            logger.warning("resolve_provider_client: profile %r failed to create an external-process client",
                           provider, exc_info=True)
            client = None
        if client is not None:
            logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
            return _route_client(req, client, final_model)
    _log_once_debug(_aux._LOGGED_UNSUPPORTED_EXTPROC_KEYS, provider,
                    "resolve_provider_client: external-process provider %s not "
                    "directly supported", provider)
    return None, None


def _resolve_registry_branch(req: _ResolveRequest) -> _ResolveResult:
    """PROVIDER_REGISTRY providers, dispatched on ``auth_type``; unknown providers log once."""
    provider = req.provider
    try:
        from hermes_cli.auth import (
            PROVIDER_REGISTRY, resolve_api_key_provider_credentials,
            resolve_external_process_provider_credentials,
        )
    except ImportError:
        logger.debug("hermes_cli.auth not available for provider %s", provider)
        return None, None
    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        _log_once_debug(_aux._LOGGED_UNKNOWN_PROVIDER_KEYS, provider,
                        "resolve_provider_client: unknown provider %r", provider)
        return None, None
    auth_type = pconfig.auth_type
    if auth_type == "api_key":
        return _resolve_api_key_branch(req, pconfig, resolve_api_key_provider_credentials)
    if auth_type == "external_process":
        return _resolve_external_process_branch(req, resolve_external_process_provider_credentials(provider))
    if auth_type == "vertex":
        client, final_model = _build_vertex_client(provider, req.model)
    elif auth_type == "aws_sdk":
        client, final_model = _build_bedrock_client(provider, req.model, raw_codex=req.raw_codex)
    elif auth_type in {"oauth_device_code", "oauth_external"}:
        # nous / openai-codex / xai-oauth already returned from their explicit branches.
        _log_once_debug(_aux._LOGGED_UNSUPPORTED_OAUTH_KEYS, provider,
                        "resolve_provider_client: OAuth provider %s not "
                        "directly supported, try 'auto'", provider)
        return None, None
    else:
        # The first occurrence surfaces a real schema-drift bug; per-call retries stay silent.
        _log_once_debug(_aux._LOGGED_UNHANDLED_AUTHTYPE_KEYS, (auth_type, provider),
                        "resolve_provider_client: unhandled auth_type %s for %s",
                        auth_type, provider)
        return None, None
    return _route_client(req, client, final_model) if client is not None else (None, None)


# Explicit providers with a dedicated branch; anything else falls through to named custom
# providers → azure-foundry → PROVIDER_REGISTRY (order preserved from the original if-chain).
_EXPLICIT_PROVIDER_BRANCHES: Dict[str, Callable[[_ResolveRequest], _ResolveResult]] = {
    "auto": _resolve_auto_branch,
    "openrouter": _resolve_openrouter_branch,
    "nous": _resolve_nous_branch,
    "openai-codex": _resolve_openai_codex_branch,
    "xai-oauth": _resolve_xai_oauth_branch,
    "custom": _resolve_custom_branch,
}


def resolve_provider_client(
    provider: str, model: str = None, async_mode: bool = False, raw_codex: bool = False,
    explicit_base_url: str = None, explicit_api_key: str = None, api_mode: str = None,
    main_runtime: Optional[Dict[str, Any]] = None, is_vision: bool = False,
    task: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Central router: return a configured client (auth, base URL, API format) for a provider + optional model.
    The client always exposes ``.chat.completions.create()``; Codex/Responses providers get an adapter.
    ``provider``: built-in name, ``custom:<name>``, "custom" (OPENAI_BASE_URL + OPENAI_API_KEY) or "auto"
    (full auto-detection chain). ``model=None`` → provider's default aux model. ``raw_codex`` → bare OpenAI
    client for ``responses.stream()`` callers. ``api_mode`` forces "codex_responses"/"chat_completions"/
    "anthropic_messages" instead of auto-detect. Returns (client, resolved_model) or (None, None)."""
    _aux._validate_proxy_env_urls()
    # Keep the pre-alias name so a custom_providers entry named like a built-in alias
    # (e.g. "kimi" → "kimi-coding") is still reachable via the named-custom branch.
    original_provider = (provider or "").strip().lower()
    provider = _aux._normalize_aux_provider(provider)
    # MoA chokepoint: "moa" is not an HTTP provider; resolve to the aggregator so direct callers don't
    # dead-end in unknown-provider. Unresolvable preset → leave untouched for the normal diagnostic.
    if provider == "moa":
        _agg_provider, _agg_model = _aux._resolve_moa_aggregator(model)
        if _agg_provider and _agg_model:
            original_provider = _agg_provider.strip().lower()
            provider = _aux._normalize_aux_provider(_agg_provider)
            model = _agg_model
            # The moa:// facade endpoint/key belong to the virtual runtime, not the aggregator.
            if explicit_base_url and str(explicit_base_url).lower().startswith("moa://"):
                explicit_base_url = None
                explicit_api_key = None
    # Model for concrete providers: caller ``model`` → catalog default (empty for OAuth-gated providers whose
    # lists drift) → configured main model (MoA → aggregator), keeping OAuth aux tasks off the Step-2 fallback.
    # Excluded: ``auto`` (a stale main slug could pair with any picked provider) and Nous + vision (the
    # Portal's tier-aware vision recommendation must win over a text-only model).
    if not model and provider != "auto" and not (provider == "nous" and is_vision):
        # ``auto`` is intentionally excluded: `_resolve_auto_route(main_runtime=...)` returns the model paired
        # with the provider it actually selected. Pre-filling an auto call from `_read_main_model()` can
        # leak a stale process-global runtime into a different provider (for example Claude model slug on
        # Codex OAuth) and override that correctly resolved model. 1. ``model`` argument (caller knew what
        # they wanted) 2. Provider's catalog default — cheap/fast model the provider registered via
        # ``ProviderProfile.default_aux_model`` or the legacy ``_API_KEY_PROVIDER_AUX_MODELS_FALLBACK``
        # dict. 3. User's main model from ``model.model`` in config.yaml. This is the load-bearing step for
        # OAuth providers: an xai-oauth user with grok-4.3 configured gets grok-4.3 for title generation
        # instead of silently dropping to whatever Step-2 fallback (#31845). When the main provider is MoA,
        # ``_read_main_model_for_aux()`` substitutes the preset's aggregator model — the preset NAME is
        # never a valid wire model id, so unset aux models default to the preset's acting model instead.
        # Each provider branch below sees a non-empty ``model`` whenever the user has *anything* configured
        # — no provider-specific empty-model guards needed. When the user has NOTHING configured (fresh
        # install, main_model also empty), the branches still hit their own missing-credentials returns and
        # ``_resolve_auto_route`` falls through to the Step-2 chain as before. Do NOT pre-fill a blank ``auto``
        # request from the config/main default here. Claude model sent to Codex after the main lane fell
        # back to gpt-5.5). Let _resolve_auto_route() return the actual current runtime model when the caller did
        # not explicitly request one. (# compression-current-model) Nous + vision is the one carve-out: the
        # branch below resolves its model from the Portal's tier-aware vision recommendation
        # (``_try_nous(vision= True)``), and ``final_model = model or default`` means anything pre-filled
        # here wins over that. The main chat model is routinely text-only (e.g. a ``:free`` chat SKU), so
        # pre-filling it sends the image to a model that cannot accept one and the Portal 404s. Leave
        # ``model`` unset and let the Portal slot through; only an explicit caller model may override it.
        model = _aux._get_aux_model_for_provider(provider) or _aux._read_main_model_for_aux() or model
    req = _ResolveRequest(
        provider, original_provider, model, async_mode, raw_codex,
        explicit_base_url, explicit_api_key, api_mode, main_runtime, is_vision, task,
    )
    branch = _EXPLICIT_PROVIDER_BRANCHES.get(provider)
    if branch is not None:
        return branch(req)
    # Named custom providers; an ImportError anywhere in the arm falls through to the built-ins.
    try:
        result = _resolve_named_custom_branch(req)
    except ImportError:
        result = None
    if result is not None:
        return result
    if provider == "azure-foundry":
        return _resolve_azure_foundry_branch(req)
    return _resolve_registry_branch(req)


# ── Public API ──────────────────────────────────────────────────────────────

def get_text_auxiliary_client(task: str = "", *, main_runtime: Optional[Dict[str, Any]] = None) -> Tuple[Optional[_aux.OpenAI], Optional[str]]:
    """Return (client, default_model_slug) for text-only aux tasks; ``task`` selects auxiliary.<task> overrides."""
    provider, model, base_url, api_key, api_mode = _aux._resolve_task_provider_model(task or None)
    return _aux.resolve_provider_client(
        provider, model=model, explicit_base_url=base_url, explicit_api_key=api_key,
        api_mode=api_mode, main_runtime=main_runtime,
    )


_VISION_AUTO_PROVIDER_ORDER = ("openrouter", "nous", "deepinfra")


def _main_model_supports_vision(provider: str, model: Optional[str]) -> bool:
    """True when ``provider``/``model`` is known to accept image input; unknown capability → True (attempt the call)."""
    try:
        from agent.image_routing import _lookup_supports_vision
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return True
    try:
        supports = _lookup_supports_vision(provider, model, load_config_readonly())
    except Exception:  # pragma: no cover - defensive
        return True
    return True if supports is None else bool(supports)


def _normalize_vision_provider(provider: Optional[str]) -> str:
    return _aux._normalize_aux_provider(provider)


def _deepinfra_strict_vision_backend(model: Optional[str]) -> Tuple[Optional[Any], Optional[str]]:
    """DeepInfra vision: default model is discovered live via default_vision_model() so no hardcoded id can rot."""
    vision_model = model or _aux._resolve_provider_vision_default("deepinfra")
    if not vision_model:
        logger.debug("Vision auto-detect: deepinfra catalog unreachable or returned no vision-tagged models — skipping")
        return None, None
    return _aux.resolve_provider_client("deepinfra", vision_model, is_vision=True)


# Strict (explicitly requested) vision backends by normalized provider name. nous MUST go
# through resolve_provider_client so anthropic/* picks wrap onto /v1/messages (a bare _try_nous
# client 404s). openai-codex has no safe default model; callers set auxiliary.<task>.model.
_STRICT_VISION_BACKENDS: Dict[str, Callable[[Optional[str]], Tuple[Optional[Any], Optional[str]]]] = {
    "copilot": lambda model: _aux.resolve_provider_client("copilot", model, is_vision=True),
    "openrouter": lambda model: _aux._try_openrouter(model=model),
    "nous": lambda model: _aux.resolve_provider_client("nous", model, is_vision=True),
    "openai-codex": lambda model: _aux.resolve_provider_client("openai-codex", model, is_vision=True),
    "anthropic": lambda model: _aux._try_anthropic(),
    "deepinfra": _deepinfra_strict_vision_backend,
    "custom": lambda model: _aux._try_custom_endpoint(),
}


def _resolve_strict_vision_backend(provider: str, model: Optional[str] = None) -> Tuple[Optional[Any], Optional[str]]:
    backend = _STRICT_VISION_BACKENDS.get(_normalize_vision_provider(provider))
    return backend(model) if backend is not None else (None, None)


def get_available_vision_backends() -> List[str]:
    """Available vision backends in auto-selection order (active provider → OpenRouter → Nous → DeepInfra).

    Single source of truth for setup, tool gating, and runtime auto-routing.
    """
    available: List[str] = []
    main_provider = _aux._read_main_provider()
    if main_provider and main_provider not in {"auto", ""}:
        if main_provider in _VISION_AUTO_PROVIDER_ORDER:
            main_ok = _aux._resolve_strict_vision_backend(main_provider)[0] is not None
        else:
            main_ok = _aux.resolve_provider_client(main_provider, _aux._read_main_model())[0] is not None
        if main_ok:
            available.append(main_provider)
    for p in _VISION_AUTO_PROVIDER_ORDER:  # skip if already covered by main provider
        if p not in available and _aux._resolve_strict_vision_backend(p)[0] is not None:
            available.append(p)
    return available


def _finalize_vision_client(
    resolved_provider: str, sync_client: Any, default_model: Optional[str],
    resolved_model: Optional[str], async_mode: bool,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Apply the explicit model override (and async wrapping) to a resolved vision client."""
    if sync_client is None:
        return resolved_provider, None, None
    final_model = resolved_model or default_model
    if async_mode:
        async_client, async_model = _aux._to_async_client(sync_client, final_model, is_vision=True)
        return resolved_provider, async_client, async_model
    return resolved_provider, sync_client, final_model


def _vision_main_provider_client(
    main_provider: str, main_model: str, runtime: Dict[str, Any], resolved_model: Optional[str],
    resolved_api_mode: Optional[str],
) -> Tuple[Optional[Any], Optional[str]]:
    """Auto-detect step 1: try the main provider; (None, None) falls through to the aggregator chain."""
    # A provider vision default (static override or catalog discovery) is a *known* multimodal
    # model; the pinned chat model usually isn't, so only fall back to it when no default exists.
    provider_vision_default = _aux._resolve_provider_vision_default(main_provider)
    vision_model = provider_vision_default or main_model
    if main_provider == "nous":
        # Nous picks its vision model from Portal tier-aware slots inside _try_nous(vision=True);
        # passing the chat model would override that and 404. Only auxiliary.vision.model may.
        sync_client, default_model = _aux._resolve_strict_vision_backend(main_provider, resolved_model or provider_vision_default)
        if sync_client is None:
            return None, None
        logger.info("Vision auto-detect: using main provider %s (%s)", main_provider, default_model or resolved_model or main_model)
        return sync_client, default_model
    if main_provider in _aux._PROVIDERS_WITHOUT_VISION:  # endpoint rejects image input entirely
        logger.debug("Vision auto-detect: skipping main provider %s (no vision support) — falling through to aggregator chain", main_provider)
        return None, None
    if not _main_model_supports_vision(main_provider, vision_model):
        # Known text-only model. Log only the provider name (CodeQL clear-text-logging FPs).
        logger.debug(
            "Vision auto-detect: skipping main provider %s (reports no vision capability) — falling through to aggregator chain",
            main_provider,
        )
        return None, None
    # Custom endpoints carry no built-in base_url/api_key: recover the live main endpoint from
    # set_runtime_main() or, with no live runtime recorded, the configured custom endpoint.
    rpc_base_url = rpc_api_key = None
    rpc_api_mode = resolved_api_mode
    if main_provider == "custom" or main_provider.startswith("custom:"):
        if runtime.get("base_url"):
            custom_base, custom_key, custom_mode = runtime.get("base_url"), runtime.get("api_key") or None, runtime.get("api_mode")
        else:
            custom_base, custom_key, custom_mode = _aux._resolve_custom_runtime()
        if custom_base:
            rpc_base_url, rpc_api_key = custom_base, custom_key
            rpc_api_mode = resolved_api_mode or custom_mode or None
    rpc_client, rpc_model = _aux.resolve_provider_client(
        main_provider, vision_model, api_mode=rpc_api_mode, explicit_base_url=rpc_base_url,
        explicit_api_key=rpc_api_key, main_runtime=runtime, is_vision=True)
    if rpc_client is None:
        return None, None
    logger.info("Vision auto-detect: using main provider %s (%s)", main_provider, rpc_model or vision_model)
    return rpc_client, rpc_model or vision_model


def _vision_auto_route(
    runtime: Dict[str, Any], resolved_model: Optional[str], resolved_api_mode: Optional[str],
    async_mode: bool,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Auto-detect order: 1. main provider + model, 2. OpenRouter, 3. Nous Portal, 4. DeepInfra, 5. stop."""
    main_provider = str(runtime.get("provider") or _aux._read_main_provider())
    main_model = str(runtime.get("model") or _aux._read_main_model())
    if main_provider.strip().lower() == "moa":
        # MoA main_model is a preset NAME, not a wire model — unwrap to the preset's aggregator
        # slot. The moa:// facade endpoint belongs to the virtual provider, not the real one.
        _agg_provider, _agg_model = _aux._resolve_moa_aggregator(main_model)
        if _agg_provider and _agg_model:
            main_provider, main_model = _agg_provider, _agg_model
            runtime = dict(runtime, base_url="", api_key="", api_mode="")
    if main_provider and main_provider not in {"auto", "", "moa"}:
        client, default_model = _vision_main_provider_client(main_provider, main_model, runtime, resolved_model, resolved_api_mode)
        if client is not None:
            return _finalize_vision_client(main_provider, client, default_model, resolved_model, async_mode)
    # Aggregators use their dedicated vision model, not the user's main model.
    for candidate in _VISION_AUTO_PROVIDER_ORDER:
        if candidate == main_provider:
            continue  # already tried above
        sync_client, default_model = _aux._resolve_strict_vision_backend(candidate)
        if sync_client is not None:
            return _finalize_vision_client(candidate, sync_client, default_model, resolved_model, async_mode)
    logger.debug("Auxiliary vision client: none available")
    return None, None, None


# ZAI vision must use the OpenAI-compatible endpoint: the Anthropic wire rejects max_tokens on
# multimodal calls (error 1210).
_ZAI_OPENAI_VISION_URLS = ("https://open.bigmodel.cn/api/paas/v4", "https://api.z.ai/api/paas/v4")


def resolve_vision_provider_client(
    provider: Optional[str] = None, model: Optional[str] = None, *, base_url: Optional[str] = None,
    api_key: Optional[str] = None, async_mode: bool = False,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """Resolve the client actually used for vision tasks.

    Direct endpoint overrides beat provider selection; explicit providers may force
    experimental backends; auto mode only tries backends known to work.
    """
    runtime = _aux._normalize_main_runtime(main_runtime)
    requested, resolved_model, resolved_base_url, resolved_api_key, resolved_api_mode = _aux._resolve_task_provider_model(
        "vision", provider, model, base_url, api_key
    )
    requested = _normalize_vision_provider(requested)
    if resolved_base_url:
        provider_for_base_override = requested if requested and requested not in {"", "auto"} else "custom"
        client, final_model = _aux.resolve_provider_client(
            provider_for_base_override, model=resolved_model, async_mode=async_mode,
            explicit_base_url=resolved_base_url, explicit_api_key=resolved_api_key,
            api_mode=resolved_api_mode, main_runtime=runtime,
        )
        return provider_for_base_override, client, (final_model if client is not None else None)
    if requested == "auto":
        return _vision_auto_route(runtime, resolved_model, resolved_api_mode, async_mode)
    if requested in _VISION_AUTO_PROVIDER_ORDER:
        sync_client, default_model = _aux._resolve_strict_vision_backend(requested, resolved_model)
        return _finalize_vision_client(requested, sync_client, default_model, resolved_model, async_mode)
    if requested == "zai":
        for _zai_url in _ZAI_OPENAI_VISION_URLS:
            client, final_model = _aux._get_cached_client(
                requested, resolved_model, async_mode, base_url=_zai_url,
                api_key=resolved_api_key or None, api_mode="chat_completions", main_runtime=runtime,
                is_vision=True,
            )
            if client is not None:
                return _finalize_vision_client(requested, client, final_model, resolved_model, async_mode)
        # Fallback: try without explicit base_url (old behavior)
    client, final_model = _aux._get_cached_client(
        requested, resolved_model, async_mode, api_mode=resolved_api_mode, main_runtime=runtime, is_vision=True,
    )
    return requested, client, (final_model if client is not None else None)


def get_auxiliary_extra_body() -> dict:
    """Return extra_body kwargs (Nous Portal product tags when Nous-backed, else {})."""
    return _aux._nous_extra_body() if _aux.auxiliary_is_nous else {}


def auxiliary_max_tokens_param(value: int, *, model: Optional[str] = None) -> dict:
    """Max-tokens kwarg for the auxiliary provider: direct OpenAI/Copilot and newer OpenAI-family
    models (by ``model`` name, so custom endpoints fronting gpt-5.x are caught) need max_completion_tokens."""
    _custom_host = _aux.base_url_hostname(_aux._current_custom_base_url()) or ""
    direct_openai_family = (
        not _aux._scoped_key_env("OPENROUTER_API_KEY") and _aux._read_nous_auth() is None
        and (_custom_host in ("api.openai.com", "api.githubcopilot.com") or _custom_host.endswith(".githubcopilot.com"))
    )
    if direct_openai_family or _aux.model_forces_max_completion_tokens(model):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
