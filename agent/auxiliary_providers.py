"""Provider credentials and client builders: Nous/xAI/Codex/API-key credential reads, the
``_try_*`` builders for OpenRouter, custom endpoints, Codex, Azure Foundry and Anthropic,
main-config readers and the ordered discovery chain.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


def _read_nous_auth() -> Optional[dict]:
    """Nous provider state dict from the credential pool or ~/.hermes/auth.json; None when not active with tokens."""
    pool_present, entry = _aux._select_pool_entry("nous")
    if pool_present:
        if entry is None:
            return None
        return {
            "access_token": getattr(entry, "access_token", ""),
            "refresh_token": getattr(entry, "refresh_token", None),
            "agent_key": getattr(entry, "agent_key", None),
            "inference_base_url": _aux._pool_runtime_base_url(entry, _aux._NOUS_DEFAULT_BASE_URL),
            "portal_base_url": getattr(entry, "portal_base_url", None),
            "client_id": getattr(entry, "client_id", None),
            "scope": getattr(entry, "scope", None),
            "token_type": getattr(entry, "token_type", "Bearer"),
            "source": "pool",
        }
    try:
        auth_path = _aux._auth_json_path()
        if not auth_path.is_file():
            return None
        data = json.loads(auth_path.read_text(encoding="utf-8-sig"))
        if data.get("active_provider") != "nous":
            return None
        provider = data.get("providers", {}).get("nous", {})
        # Must have at least an access_token or agent_key.
        if not provider.get("agent_key") and not provider.get("access_token"):
            return None
        return provider
    except Exception as exc:
        logger.debug("Could not read Nous auth: %s", exc)
        return None


def _nous_api_key(provider: dict) -> str:
    """Extract a usable Nous inference JWT from stored auth state."""
    from hermes_cli.auth import _nous_invoke_jwt_is_usable
    for token_key, expiry_key in (("agent_key", "agent_key_expires_at"), ("access_token", "expires_at")):
        token = provider.get(token_key)
        if not isinstance(token, str) or not token.strip():
            continue
        if _nous_invoke_jwt_is_usable(token, scope=provider.get("scope"), expires_at=provider.get(expiry_key)):
            return token
    return ""


def _resolve_nous_pool_runtime_api(*, force_refresh: bool = False) -> Optional[tuple[str, str]]:
    """Resolve Nous auxiliary credentials from the selected pool entry."""
    try:
        from hermes_cli.auth import _agent_key_is_usable
        pool = _aux.load_pool("nous")
    except Exception as exc:
        logger.debug("Auxiliary Nous pool credential resolution failed: %s", exc)
        return None
    if not pool or not pool.has_credentials():
        return None
    try:
        entry = pool.select()
    except Exception as exc:
        logger.debug("Auxiliary Nous pool selection failed: %s", exc)
        return None
    if entry is None:
        return None

    def _entry_state(e: Any) -> Dict[str, Any]:
        return {k: getattr(e, k, None) for k in (
            "agent_key", "agent_key_expires_at", "access_token", "expires_at", "scope")}

    if force_refresh or not _agent_key_is_usable(_entry_state(entry), _aux._nous_min_key_ttl_seconds()):
        try:
            refreshed = pool.try_refresh_current()
        except Exception as exc:
            logger.debug("Auxiliary Nous pool refresh failed: %s", exc)
            refreshed = None
        if refreshed is None:
            return None
        entry = refreshed
    api_key = _nous_api_key(_entry_state(entry))
    base_url = _aux._pool_runtime_base_url(entry, _aux._NOUS_DEFAULT_BASE_URL)
    if not api_key or not base_url:
        return None
    return api_key, base_url


def _resolve_nous_runtime_api(
    *, force_refresh: bool = False, stale_access_token: Optional[str] = None
) -> Optional[tuple[str, str]]:
    """Fresh Nous runtime credentials (pool first, then auth store + JWT refresh) — mirrors the main
    agent's 401 recovery. ``stale_access_token`` is the bearer that just 401'd; with ``force_refresh``
    it lets the auth store adopt a sibling process's rotation instead of re-POSTing the shared grant."""
    pooled = _resolve_nous_pool_runtime_api(force_refresh=force_refresh)
    if pooled is not None:
        return pooled
    try:
        from hermes_cli.auth import resolve_nous_runtime_credentials
        creds = resolve_nous_runtime_credentials(
            timeout_seconds=_aux.env_float("HERMES_NOUS_TIMEOUT_SECONDS", 15),
            force_refresh=force_refresh,
            stale_access_token=stale_access_token or None,
        )
    except Exception as exc:
        logger.debug("Auxiliary Nous runtime credential resolution failed: %s", exc)
        return None
    return _creds_pair(creds)


def _creds_pair(creds: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """``(api_key, base_url)`` from a runtime-credentials dict, or None when either is missing."""
    api_key = str(creds.get("api_key") or "").strip()
    base_url = str(creds.get("base_url") or "").strip().rstrip("/")
    if not api_key or not base_url:
        return None
    return api_key, base_url


def _resolve_xai_oauth_for_aux() -> Optional[Tuple[str, str]]:
    """Fresh xAI OAuth (api_key, base_url) for aux clients, or None.

    Pool first (some xAI OAuth logins exist only as pool entries), then the singleton auth-store resolver.
    """
    try:
        from hermes_cli.auth import DEFAULT_XAI_OAUTH_BASE_URL, _xai_validate_inference_base_url
        pool = _aux.load_pool("xai-oauth")
        if pool and pool.has_credentials():
            entry = pool.select()
            if entry is not None:
                api_key = str(
                    getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "") or ""
                ).strip()
                _url = lambda v: str(v or "").strip().rstrip("/")  # noqa: E731
                base_url = _xai_validate_inference_base_url(
                    _url(_aux._scoped_key_env("HERMES_XAI_BASE_URL"))
                    or _url(_aux._scoped_key_env("XAI_BASE_URL"))
                    or _url(getattr(entry, "runtime_base_url", None))
                    or _url(getattr(entry, "base_url", None)),
                    fallback=DEFAULT_XAI_OAUTH_BASE_URL,
                )
                if api_key and base_url:
                    return api_key, base_url
    except Exception as exc:
        logger.debug("Auxiliary xAI OAuth pool credential resolution failed: %s", exc)
    try:
        from hermes_cli.auth import resolve_xai_oauth_runtime_credentials
        creds = resolve_xai_oauth_runtime_credentials()
    except Exception as exc:
        logger.debug("Auxiliary xAI OAuth runtime credential resolution failed: %s", exc)
        return None
    return _creds_pair(creds)


def _read_codex_access_token() -> Optional[str]:
    """Valid, non-expired Codex OAuth access token; an exhausted pool falls back to the profile's auth.json token."""
    pool_present, entry = _aux._select_pool_entry("openai-codex")
    if pool_present:
        token = _aux._pool_runtime_api_key(entry)
        if token:
            return token
    try:
        from hermes_cli.auth import _read_codex_tokens
        access_token = _read_codex_tokens().get("tokens", {}).get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return None
        # Expired JWTs would block the auto chain and prevent fallback to working providers.
        try:
            import base64
            payload = access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
            if exp and time.time() > exp:
                logger.debug("Codex access token expired (exp=%s), skipping", exp)
                return None
        except Exception:
            pass  # Non-JWT token or decode error — use as-is
        return access_token.strip()
    except Exception as exc:
        logger.debug("Could not read Codex auth for auxiliary client: %s", exc)
        return None


def _resolve_api_key_provider() -> Tuple[Optional[_aux.OpenAI], Optional[str]]:
    """Try each API-key provider in PROVIDER_REGISTRY order; (client, model) or (None, None)."""
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("Could not import PROVIDER_REGISTRY for API-key fallback")
        return None, None
    for provider_id, pconfig in PROVIDER_REGISTRY.items():
        if pconfig.auth_type != "api_key":
            continue
        if _aux._is_provider_unhealthy(provider_id):
            logger.debug("Auxiliary api-key chain: %s is unhealthy, skipping", provider_id)
            continue
        if provider_id == "anthropic":
            # Explicit-config gate: Claude Code credentials must not silently become aux fallback.
            with contextlib.suppress(ImportError):
                from hermes_cli.auth import is_provider_explicitly_configured
                if not is_provider_explicitly_configured("anthropic"):
                    continue
            return _aux._try_anthropic()
        pool_present, entry = _aux._select_pool_entry(provider_id)
        if pool_present:
            api_key = _aux._pool_runtime_api_key(entry)
            if not api_key:
                continue
            raw_base_url = _aux._pool_runtime_base_url(entry, pconfig.inference_base_url) or pconfig.inference_base_url
            via = " via pool"
        else:
            creds = resolve_api_key_provider_credentials(provider_id)
            api_key = str(creds.get("api_key", "")).strip()
            if not api_key:
                continue
            raw_base_url = str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
            via = ""
        # The session's own endpoint wins for its provider: the key was issued for that gateway, and
        # sending it to the registry default 401s, then quarantines the provider the main model is on.
        runtime = _normalize_main_runtime(None)
        if runtime.get("provider") == provider_id and runtime.get("base_url"):
            raw_base_url = runtime["base_url"].rstrip("/")
            if isinstance(runtime.get("api_key"), str) and runtime["api_key"]:
                api_key = runtime["api_key"]
            via = " (session endpoint)"
        model = _aux._get_aux_model_for_provider(provider_id) or None
        if model is None:
            continue  # skip provider if we don't know a valid aux model
        logger.debug("Auxiliary text client: %s (%s)%s", pconfig.name, model, via)
        # Native Gemini, else OpenAI-wire + Anthropic rewrap.
        base_url = _aux._to_openai_base_url(raw_base_url)
        if provider_id == "gemini":
            from agent.gemini_native_adapter import GeminiNativeClient, is_native_gemini_base_url
            if is_native_gemini_base_url(base_url):
                return GeminiNativeClient(api_key=api_key, base_url=base_url), model
        if _aux.base_url_host_matches(base_url, "api.kimi.com"):
            headers = {"User-Agent": "claude-code/0.1.0"}
        elif _aux.base_url_host_matches(base_url, "githubcopilot.com"):
            from hermes_cli.models import copilot_default_headers
            headers = copilot_default_headers()
        elif _aux.base_url_host_matches(base_url, "integrate.api.nvidia.com"):
            headers = _aux.build_nvidia_nim_headers(base_url)
        else:
            headers = _profile_default_headers(provider_id)
        extra = {"default_headers": headers} if headers else {}
        merged = _aux._apply_user_default_headers(extra.get("default_headers"))
        if merged:
            extra["default_headers"] = merged
        client = _aux._create_openai_client(api_key=api_key, base_url=base_url, **extra)
        return _aux._maybe_wrap_anthropic(client, model, api_key, raw_base_url), model
    return None, None


def _endpoint_default_headers(
    base_url: str, provider: str, *, is_vision: bool = False, xai: bool = False,
) -> Optional[dict]:
    """Provider-specific client headers by endpoint host, merged with user ``model.default_headers``.

    Kimi Code needs the claude-code User-Agent; Copilot needs its request headers
    (``is_vision`` adds Copilot-Vision-Request); NVIDIA NIM and (optionally) xAI have
    their own fingerprints; anything else falls back to the provider profile.
    """
    if _aux.base_url_host_matches(base_url, "api.kimi.com"):
        headers: dict = {"User-Agent": "claude-code/0.1.0"}
    elif _aux.base_url_host_matches(base_url, "githubcopilot.com"):
        from hermes_cli.copilot_auth import copilot_request_headers
        headers = dict(copilot_request_headers(is_agent_turn=True, is_vision=is_vision))
    elif _aux.base_url_host_matches(base_url, "integrate.api.nvidia.com"):
        headers = dict(_aux.build_nvidia_nim_headers(base_url))
    elif xai and _aux.base_url_host_matches(base_url, "x.ai"):
        from tools.xai_http import hermes_xai_default_headers
        headers = dict(hermes_xai_default_headers())
    else:
        headers = _profile_default_headers(provider) or {}
    return _aux._apply_user_default_headers(headers or None) or None


def _profile_default_headers(provider: str) -> Optional[dict]:
    """Client-level attribution headers from the provider profile (e.g. GMI User-Agent), or None."""
    if not provider:
        return None
    with contextlib.suppress(Exception):
        from providers import get_provider_profile
        profile = get_provider_profile(provider)
        if profile and profile.default_headers:
            return dict(profile.default_headers)
    return None


# Provider resolution helpers

_paid_lane_warned: set = set()


def _is_free_model(model: Optional[str]) -> bool:
    """True when ``model`` is a free SKU (``:free`` suffix or ``stealth/`` prefix) — naming-convention trust."""
    if not model:
        return False
    normalized = str(model).strip()
    return normalized.endswith(":free") or normalized.startswith("stealth/")


def _aux_openrouter_settings() -> Tuple[bool, str]:
    """Read (free_only, openrouter_model) from config; (False, _OPENROUTER_MODEL) on failure."""
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        cfg = load_config_readonly()
        free_only = bool(cfg_get(cfg, "auxiliary", "free_only", default=False))
        val = cfg_get(cfg, "auxiliary", "openrouter_model")
        model = val.strip() if isinstance(val, str) and val.strip() else _aux._OPENROUTER_MODEL
        return free_only, model
    except Exception:
        return False, _aux._OPENROUTER_MODEL


def _warn_paid_lane_once(model: str) -> None:
    """Log a WARNING the first time a non-free OpenRouter model is engaged."""
    if model in _paid_lane_warned:
        return
    _paid_lane_warned.add(model)
    logger.warning(
        "Auxiliary client: PAID lane engaged for auxiliary task — OpenRouter fallback model %r is not "
        "a :free SKU and may incur real spend. Set auxiliary.free_only: true to restrict auxiliary "
        "fallbacks to free models, or auxiliary.openrouter_model to a :free model.", model,
    )


def _try_openrouter(explicit_api_key: str = None, model: str = None) -> Tuple[Optional[_aux.OpenAI], Optional[str]]:
    free_only, cfg_model = _aux_openrouter_settings()
    or_model = model or cfg_model
    if free_only and not _is_free_model(or_model):
        logger.warning(
            "Auxiliary client: auxiliary.free_only is enabled but the OpenRouter fallback model %r is "
            "not a :free SKU — skipping the OpenRouter fallback. Set auxiliary.openrouter_model to a "
            ":free model (e.g. nvidia/nemotron-3-ultra-550b-a55b:free) or disable auxiliary.free_only.",
            or_model,
        )
        return None, None
    if not _is_free_model(or_model):
        _warn_paid_lane_once(or_model)
    pool_present, entry = _aux._select_pool_entry("openrouter")
    if pool_present:
        or_key = explicit_api_key or _aux._pool_runtime_api_key(entry)
        if or_key:
            base_url = _aux._pool_runtime_base_url(entry, _aux.OPENROUTER_BASE_URL) or _aux.OPENROUTER_BASE_URL
            logger.debug("Auxiliary client: OpenRouter via pool")
            return _aux._create_openai_client(
                api_key=or_key, base_url=base_url, default_headers=_aux.build_or_headers()
            ), or_model
        # Exhausted pool: fall through to OPENROUTER_API_KEY rather than fail.
        logger.debug("Auxiliary client: OpenRouter pool exhausted, trying OPENROUTER_API_KEY")
    or_key = explicit_api_key or _aux._scoped_key_env("OPENROUTER_API_KEY")
    if not or_key:
        _aux._mark_provider_unhealthy("openrouter", ttl=60)
        return None, None
    logger.debug("Auxiliary client: OpenRouter")
    return _aux._create_openai_client(
        api_key=or_key, base_url=_aux.OPENROUTER_BASE_URL, default_headers=_aux.build_or_headers()
    ), or_model


def _describe_openrouter_unavailable(model: str = None) -> str:
    """Return the policy or credential reason OpenRouter was unavailable."""
    free_only, cfg_model = _aux_openrouter_settings()
    or_model = model or cfg_model
    if free_only and not _is_free_model(or_model):
        return (
            f"auxiliary.free_only rejected non-free model {or_model!r}; "
            "the request was skipped before provider availability checks"
        )
    pool_present, entry = _aux._select_pool_entry("openrouter")
    if pool_present:
        if entry is None:
            return "OpenRouter credential pool has no usable entries (credentials may be exhausted)"
        if not _aux._pool_runtime_api_key(entry):
            return "OpenRouter credential pool entry is missing a runtime API key"
    if not _aux._scoped_key_env("OPENROUTER_API_KEY"):
        return "OPENROUTER_API_KEY not set"
    return "no usable OpenRouter credentials found"


def _refresh_nous_recommended_model(*, vision: bool, stale_model: Optional[str]) -> Optional[str]:
    """Fresh Portal recommended model after a stale-model 404 (long-lived processes pin dropped models).

    Returns the fresh recommendation, else ``_NOUS_MODEL``, whichever differs from ``stale_model``; None if neither.
    """
    stale = (stale_model or "").strip().lower()
    fresh: Optional[str] = None
    try:
        from hermes_cli.models import get_nous_recommended_aux_model
        fresh = get_nous_recommended_aux_model(vision=vision, force_refresh=True)
    except Exception as exc:
        logger.debug("Nous recommended-model refresh failed (%s); using default %s", exc, _aux._NOUS_MODEL)
    if fresh and fresh.strip().lower() != stale:
        return fresh
    return _aux._NOUS_MODEL if _aux._NOUS_MODEL.strip().lower() != stale else None


def _read_main_field(field: str, *, readonly: bool, lower: bool = False) -> str:
    """Main ``model.<field>``: runtime override (``set_runtime_main``) first, then config.yaml.

    The override wins so "active main model" gates see the live CLI/gateway runtime, not the persisted
    default. ``readonly`` picks ``load_config_readonly`` (model/provider) vs ``load_config`` (api_key/base_url).
    """
    override = _aux._runtime_main_value(field)
    if isinstance(override, str) and override.strip():
        value = override.strip()
        return value.lower() if lower else value
    with contextlib.suppress(Exception):
        from hermes_cli import config as _cfg_mod
        cfg = (_cfg_mod.load_config_readonly if readonly else _cfg_mod.load_config)()
        model_cfg = cfg.get("model", {})
        if field == "model" and isinstance(model_cfg, str) and model_cfg.strip():
            return model_cfg.strip()
        if isinstance(model_cfg, dict):
            value = model_cfg.get("default" if field == "model" else field, "")
            if isinstance(value, str) and value.strip():
                value = value.strip()
                return value.lower() if lower else value
    return ""


# Module-level callables (tests patch them): model/provider (lowercased) read the readonly config;
# api_key/base_url read the full config so ``custom`` aux tasks can inherit main creds.
_read_main_model = functools.partial(_read_main_field, "model", readonly=True)
_read_main_provider = functools.partial(_read_main_field, "provider", readonly=True, lower=True)
_read_main_api_key = functools.partial(_read_main_field, "api_key", readonly=False)
_read_main_base_url = functools.partial(_read_main_field, "base_url", readonly=False)


def _resolve_moa_aggregator(preset_name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """MoA preset → aggregator (provider, model); (None, None) if unresolvable. None/"" = default preset.

    "moa" is virtual — aux tasks skip the fan-out and use the aggregator slot; shared so lookup can't drift.
    """
    try:
        from hermes_cli.config import load_config
        from hermes_cli.moa_config import resolve_moa_preset
        preset = resolve_moa_preset(load_config().get("moa") or {}, preset_name or None)
        agg = preset.get("aggregator") or {}
        agg_provider = str(agg.get("provider") or "").strip()
        agg_model = str(agg.get("model") or "").strip()
        if agg_provider and agg_model and agg_provider.lower() != "moa":
            return agg_provider, agg_model
    except Exception:
        logger.debug("MoA aggregator resolution failed for preset %r", preset_name, exc_info=True)
    return None, None


def _read_main_model_for_aux() -> str:
    """Main model with MoA presets unwrapped to the aggregator's model; "" when unresolvable (a preset name would 400)."""
    model = _aux._read_main_model()
    if (_aux._read_main_provider() or "").strip().lower() == "moa":
        _, agg_model = _resolve_moa_aggregator(model)
        return agg_model or ""
    return model


def _read_main_api_key_if_same_host(aux_base_url: str) -> str:
    """Main api_key only when *aux_base_url* shares the main base_url's host.

    Unconditional inheritance would leak the credential to any misconfigured host; mismatch keeps ``no-key-required`` → 401.
    """
    aux_host = _aux.base_url_hostname(aux_base_url)
    if not aux_host or aux_host != _aux.base_url_hostname(_read_main_base_url()):
        return ""
    return _read_main_api_key()


def _resolve_custom_runtime() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve the active custom/main endpoint like the main CLI (env OPENAI_BASE_URL or config-saved)."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested="custom")
    except Exception as exc:
        logger.debug("Auxiliary client: custom runtime resolution failed: %s", exc)
        runtime = None
    if not isinstance(runtime, dict):
        # Base URL is per-profile like the key one line below (a scoped key must not hit the default's proxy).
        openai_base = _aux._scoped_key_env("OPENAI_BASE_URL").rstrip("/")
        if not openai_base:
            return None, None, None
        runtime = {"base_url": openai_base, "api_key": _aux._scoped_key_env("OPENAI_API_KEY")}
    custom_base = runtime.get("base_url")
    custom_key = runtime.get("api_key")
    custom_mode = runtime.get("api_mode")
    if not isinstance(custom_base, str) or not custom_base.strip():
        return None, None, None
    custom_base = custom_base.strip().rstrip("/")
    if _aux.base_url_host_matches(custom_base, "openrouter.ai"):
        return None, None, None  # requested='custom' falls back to OpenRouter when unconfigured.
    # Local servers (Ollama, vLLM, ...) ignore auth but the SDK needs a non-empty key.
    # Use a placeholder key — the OpenAI SDK requires a non-empty string but local servers ignore the
    # Authorization header. Same fix as cli.py _ensure_runtime_credentials() (PR #2556).
    if not isinstance(custom_key, str) or not custom_key.strip():
        custom_key = "no-key-required"
    if not isinstance(custom_mode, str) or not custom_mode.strip():
        custom_mode = None
    return custom_base, custom_key.strip(), custom_mode


def _current_custom_base_url() -> str:
    custom_base, _, _ = _aux._resolve_custom_runtime()
    return custom_base or ""


def _validate_proxy_env_urls() -> None:
    """Fail fast on malformed proxy env URLs (a shell typo like ``:6153export`` otherwise surfaces as a cryptic httpx ``Invalid port``)."""
    from urllib.parse import urlparse
    _aux.normalize_proxy_env_vars()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = str(os.environ.get(key) or "").strip()
        if not value:
            continue
        try:
            parsed = urlparse(value)
            if parsed.scheme:
                _ = parsed.port          # raises ValueError for e.g. '6153export'
        except ValueError as exc:
            raise RuntimeError(
                f"Malformed proxy environment variable {key}={value!r}. "
                "Fix or unset your proxy settings and try again."
            ) from exc


def _validate_base_url(base_url: str) -> None:
    """Reject obviously broken custom endpoint URLs before they reach httpx."""
    from urllib.parse import urlparse
    candidate = str(base_url or "").strip()
    if not candidate or candidate.startswith("acp://"):
        return
    try:
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"}:
            _ = parsed.port              # raises ValueError for malformed ports
    except ValueError as exc:
        raise RuntimeError(
            f"Malformed custom endpoint URL: {candidate!r}. "
            "Run `hermes setup` or `hermes model` and enter a valid http(s) base URL."
        ) from exc


def _try_custom_endpoint() -> Tuple[Optional[Any], Optional[str]]:
    runtime = _aux._resolve_custom_runtime()
    custom_base, custom_key, custom_mode = (*runtime, None) if len(runtime) == 2 else runtime
    if not custom_base or not custom_key:
        return None, None
    if custom_base.lower().startswith(_aux._CODEX_AUX_BASE_URL.lower()):
        return None, None
    model = _read_main_model_for_aux() or "gpt-4o-mini"
    logger.debug("Auxiliary client: custom endpoint (%s, api_mode=%s)", model, custom_mode or "chat_completions")
    _clean_base, _dq = _aux._extract_url_query_params(custom_base)
    _extra = {"default_query": _dq} if _dq else {}
    # User model.default_headers override SDK fingerprint headers (as on the main client) for strict gateways/WAFs.
    _custom_headers = _aux._apply_user_default_headers(None)
    if _custom_headers:
        _extra["default_headers"] = _custom_headers
    if custom_mode == "codex_responses":
        real_client = _aux._create_openai_client(api_key=custom_key, base_url=_clean_base, **_extra)
        return _aux.CodexAuxiliaryClient(real_client, model), model
    if custom_mode == "anthropic_messages":
        # Third-party Anthropic-compatible gateway — never OAuth (that's api.anthropic.com only).
        try:
            from agent.anthropic_adapter import build_anthropic_client
            real_client = build_anthropic_client(custom_key, custom_base)
        except ImportError:
            logger.warning(
                "Custom endpoint declares api_mode=anthropic_messages but the "
                "anthropic SDK is not installed — falling back to OpenAI-wire."
            )
            return _aux._create_openai_client(api_key=custom_key, base_url=_clean_base, **_extra), model
        return _aux.AnthropicAuxiliaryClient(real_client, model, custom_key, custom_base, is_oauth=False), model
    # URL-based anthropic detection for custom endpoints without explicit api_mode.
    _fallback_client = _aux._create_openai_client(api_key=custom_key, base_url=_clean_base, **_extra)
    return _aux._maybe_wrap_anthropic(_fallback_client, model, custom_key, custom_base, custom_mode), model


def _build_xai_oauth_aux_client(model: str) -> Tuple[Optional[Any], Optional[str]]:
    """CodexAuxiliaryClient for xAI Grok OAuth (Responses API); (None, None) if not authed.

    Caller must pass an explicit model — a pinned Grok default would rot as xAI's allowlist drifts.
    """
    if not model:
        logger.warning(
            "Auxiliary client: xai-oauth requested without a model; "
            "pass model explicitly (auxiliary.<task>.model in config.yaml)."
        )
        return None, None
    resolved = _resolve_xai_oauth_for_aux()
    if resolved is None:
        return None, None
    api_key, base_url = resolved
    logger.debug("Auxiliary client: xAI OAuth (%s via Responses API)", model)
    from tools.xai_http import hermes_xai_default_headers
    real_client = _aux._create_openai_client(
        api_key=api_key, base_url=base_url, default_headers=hermes_xai_default_headers()
    )
    return _aux.CodexAuxiliaryClient(real_client, model), model


def _codex_base_url_override() -> str:
    """Profile-scoped ``HERMES_CODEX_BASE_URL`` (same read as the API-key env vars: under a
    multiplexer the routed profile's .env decides the endpoint, never a sibling's process env)."""
    return _aux._scoped_key_env("HERMES_CODEX_BASE_URL").rstrip("/")


def _build_codex_client(model: str) -> Tuple[Optional[Any], Optional[str]]:
    """CodexAuxiliaryClient for an explicit model; (None, None) without a Codex OAuth token.

    No auto-selected default: the Codex model allow-list is undocumented and drifts.
    """
    if not model:
        logger.warning(
            "Auxiliary client: openai-codex requested without a model; "
            "pass model explicitly (auxiliary.<task>.model in config.yaml)."
        )
        return None, None
    pool_present, entry = _aux._select_pool_entry("openai-codex")
    codex_token = _aux._pool_runtime_api_key(entry) if pool_present else None
    codex_override = _codex_base_url_override()
    if codex_token:
        base_url = codex_override or _aux._pool_runtime_base_url(entry, _aux._CODEX_AUX_BASE_URL) or _aux._CODEX_AUX_BASE_URL
    else:
        codex_token = _aux._read_codex_access_token()
        if not codex_token:
            return None, None
        base_url = codex_override or _aux._CODEX_AUX_BASE_URL
    logger.debug("Auxiliary client: Codex OAuth (%s via Responses API)", model)
    real_client = _aux._create_openai_client(
        api_key=codex_token, base_url=base_url,
        default_headers=_aux._codex_cloudflare_headers(codex_token, base_url=base_url),
    )
    return _aux.CodexAuxiliaryClient(real_client, model), model


def _try_azure_foundry(
    *, model: Optional[str] = None, explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None, api_mode: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Azure Foundry aux client via the main agent's ``_resolve_azure_foundry_runtime`` (api_key vs Entra
    callable bearer, per-model api_mode, base_url overrides). Returns ``(client, model)`` or ``(None, None)``."""
    try:
        from hermes_cli.runtime_provider import _resolve_azure_foundry_runtime
        from hermes_cli.auth import AuthError
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return None, None
    try:
        cfg = load_config_readonly()
        model_cfg = cfg.get("model") if isinstance(cfg, dict) else {}
        if not isinstance(model_cfg, dict):
            model_cfg = {}
    except Exception:
        model_cfg = {}
    try:
        runtime = _resolve_azure_foundry_runtime(
            requested_provider="azure-foundry", model_cfg=model_cfg,
            explicit_api_key=explicit_api_key, explicit_base_url=explicit_base_url,
            target_model=model,
        )
    except AuthError as exc:
        logger.debug("Auxiliary azure-foundry: %s", exc)
        return None, None
    except Exception as exc:
        logger.debug("Auxiliary azure-foundry runtime error: %s", exc)
        return None, None
    api_key = runtime.get("api_key")
    base_url = str(runtime.get("base_url", "") or "")
    runtime_api_mode = api_mode or runtime.get("api_mode") or "chat_completions"
    # api_key may be a callable token provider; bail only on None/"".
    if not (callable(api_key) or api_key) or not base_url:
        return None, None
    final_model = _aux._normalize_resolved_model(model or str(model_cfg.get("default") or ""), "azure-foundry")
    if not final_model:
        # No fallback aux model for Azure (needs a deployment name): let the auto chain fall through instead of 404ing.
        logger.debug(
            "Auxiliary azure-foundry: no model resolved (model=%r, default=%r)",
            model, model_cfg.get("default"),
        )
        return None, None
    # The SDK drops api-version query params from the base URL; pass via default_query.
    _clean_base, _dq = _aux._extract_url_query_params(base_url)
    extra: Dict[str, Any] = {"default_query": _dq} if _dq else {}
    client = _aux._create_openai_client(api_key=api_key, base_url=_clean_base, **extra)
    if runtime_api_mode == "codex_responses":
        return _aux.CodexAuxiliaryClient(client, final_model), final_model
    if runtime_api_mode == "anthropic_messages":
        # api_key forwarded verbatim (string or Entra callable; build_anthropic_client installs the bearer hook).
        return _aux._maybe_wrap_anthropic(client, final_model, api_key, base_url, runtime_api_mode), final_model
    return client, final_model


def _try_anthropic(explicit_api_key: str = None) -> Tuple[Optional[Any], Optional[str]]:
    try:
        from agent.anthropic_adapter import build_anthropic_client
        from agent.anthropic_credentials import resolve_anthropic_token
    except ImportError:
        return None, None
    pool_present, entry = _aux._select_pool_entry("anthropic")
    if pool_present and entry is not None:
        token = explicit_api_key or _aux._pool_runtime_api_key(entry)
    else:
        # Pool absent/empty: legacy resolver so a dead pool entry can't wedge aux tasks when a standalone credential exists.
        entry = None
        token = explicit_api_key or resolve_anthropic_token()
    if not token:
        return None, None
    # Honor config.yaml model.base_url only when provider is anthropic AND the URL is
    # Anthropic-compatible; a foreign host (Codex, OpenRouter) would 401 every aux call.
    base_url = _aux._pool_runtime_base_url(entry, _aux._ANTHROPIC_DEFAULT_BASE_URL) if pool_present else _aux._ANTHROPIC_DEFAULT_BASE_URL
    with contextlib.suppress(Exception):
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        model_cfg = cfg.get("model")
        if isinstance(model_cfg, dict):
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            if cfg_provider == "anthropic":
                cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
                if cfg_base_url and _aux._is_anthropic_compatible_host(cfg_base_url):
                    base_url = cfg_base_url
    from agent.anthropic_credentials import _is_oauth_token
    is_oauth = _is_oauth_token(token)
    model = _aux._get_aux_model_for_provider("anthropic") or "claude-haiku-4-5-20251001"
    if _aux._aux_probe_active():
        # Probe: token + adapter import resolved; skip real client construction.
        return _aux._AuxProbeClientStub(api_key="", base_url=base_url), model
    logger.debug("Auxiliary client: Anthropic native (%s) at %s (oauth=%s)", model, base_url, is_oauth)
    try:
        real_client = build_anthropic_client(token, base_url)
    except ImportError:
        return None, None  # Adapter imports fine but the anthropic SDK itself is missing.
    return _aux.AnthropicAuxiliaryClient(real_client, model, token, base_url, is_oauth=is_oauth), model


_MAIN_RUNTIME_FIELDS = ("provider", "model", "base_url", "api_key", "api_mode", "auth_mode")
_MAIN_RUNTIME_CONTEXT_FIELDS = _MAIN_RUNTIME_FIELDS + ("requested_provider",)


def _normalize_main_runtime(main_runtime: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a sanitized copy of a live main-runtime override.

    ``api_key`` may be a zero-arg callable (Entra ID token provider, accepted by the OpenAI SDK)
    — preserved as-is so aux clients share main-agent auth.
    """
    if main_runtime is None:
        # Context-local state first; compat mirrors may hold another concurrent session's endpoint/key.
        main_runtime = _aux._RUNTIME_MAIN_CONTEXT.get()
        if main_runtime is None:
            main_runtime = _aux._compat_runtime_main()
    if not isinstance(main_runtime, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for field in _MAIN_RUNTIME_CONTEXT_FIELDS:
        value = main_runtime.get(field)
        if field == "api_key" and callable(value) and not isinstance(value, str):
            normalized[field] = value
        elif isinstance(value, str) and value.strip():
            normalized[field] = value.strip()
    for identity_field in ("provider", "requested_provider"):
        identity = normalized.get(identity_field)
        if isinstance(identity, str):
            normalized[identity_field] = identity.lower()
    return normalized


def _get_provider_chain() -> List[tuple]:
    """Ordered provider detection chain, built at call time so ``_try_*`` patches are picked up.

    ``openai-codex`` is deliberately absent (shifting allow-list breaks guessed-model fallback).
    """
    return [
        ("openrouter", _aux._try_openrouter), ("nous", _aux._try_nous),
        ("local/custom", _aux._try_custom_endpoint), ("api-key", _aux._resolve_api_key_provider),
    ]


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
