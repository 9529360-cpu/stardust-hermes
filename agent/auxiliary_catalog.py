"""Provider and model facts for auxiliary routing: provider aliases, temperature and
compaction rules per model, fast-model and per-provider defaults, vision defaults, request
headers (OpenRouter, NVIDIA NIM, AI Gateway, Nous), base-URL and credential-pool helpers.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


_PROVIDER_ALIASES = {
    "google": "gemini", "google-gemini": "gemini", "google-ai-studio": "gemini",
    "x-ai": "xai", "x.ai": "xai", "grok": "xai",
    "glm": "zai", "z-ai": "zai", "z.ai": "zai", "zhipu": "zai",
    "kimi": "kimi-coding", "moonshot": "kimi-coding",
    "kimi-cn": "kimi-coding-cn", "moonshot-cn": "kimi-coding-cn",
    "gmi-cloud": "gmi", "gmicloud": "gmi",
    "actual-computer": "actual", "actualcomputer": "actual", "aci": "actual",
    "minimax-china": "minimax-cn", "minimax_cn": "minimax-cn",
    "claude": "anthropic", "claude-code": "anthropic",
    "github": "copilot", "github-copilot": "copilot", "github-model": "copilot", "github-models": "copilot",
    "github-copilot-acp": "copilot-acp", "copilot-acp-agent": "copilot-acp",
    "tencent": "tencent-tokenhub", "tokenhub": "tencent-tokenhub", "tencent-cloud": "tencent-tokenhub",
    "tencentmaas": "tencent-tokenhub",
    "tokenplan": "tencent-tokenplan", "tencent-lkeap": "tencent-tokenplan",
}


def _normalize_aux_provider(provider: Optional[str]) -> str:
    normalized = (provider or "auto").strip().lower()
    if normalized.startswith("custom:"):
        suffix = normalized.split(":", 1)[1].strip()
        if not suffix:
            return "custom"
        normalized = suffix
    if normalized == "codex":
        return "openai-codex"
    if normalized == "main":
        # Resolve to the actual main provider so named custom providers work.
        main_prov = (_aux._read_main_provider() or "").strip().lower()
        if not main_prov or main_prov in {"auto", "main"}:
            return "custom"
        normalized = main_prov
    return _PROVIDER_ALIASES.get(normalized, normalized)


# Sentinel from _fixed_temperature_for_model(): callers strip ``temperature`` entirely.
# Kimi/Moonshot manage it server-side — any value can conflict with gateway mode selection.
OMIT_TEMPERATURE: object = object()


def _bare_model(model: Optional[str]) -> str:
    """Lowercased model slug with any ``vendor/`` prefix stripped."""
    return (model or "").strip().lower().rsplit("/", 1)[-1]


def _is_kimi_model(model: Optional[str]) -> bool:
    """True for any Kimi / Moonshot model that manages temperature server-side."""
    bare = _bare_model(model)
    return bare.startswith("kimi-") or bare == "kimi"


def _is_arcee_trinity_thinking(model: Optional[str]) -> bool:
    """True for Arcee Trinity Large Thinking (direct or via OpenRouter)."""
    return _bare_model(model) == "trinity-large-thinking"


# Codex OAuth hard-caps gpt-5.4/5.5/5.6 and gpt-6 Astra at 272K (raw API/OpenRouter expose 1.05M);
# the default 50% trigger would compact at ~136K, so raise to 85% (~231K).
_CODEX_GPT54_GPT55_COMPACTION_THRESHOLD = 0.85
# gpt-5.3-codex-spark: Codex-OAuth-only, native 128K; 70% (~90K) leaves summary headroom.
_CODEX_SPARK_COMPACTION_THRESHOLD = 0.70


def _is_codex_gpt54_or_gpt55(model: Optional[str], provider: Optional[str] = None) -> bool:
    """True for gpt-5.4/5.5/5.6, gpt-6 Astra (and the Daybreak Sol alias) on the Codex OAuth route only.

    Other routes expose a larger window for the same slug and keep the user's threshold.
    Prefix-matched so ``-pro`` and dated snapshots track every 272K-capped family; ``-900k``
    picker variants are excluded. Astra is substring-matched (any slug containing ``astra``
    without ``900k``). Name kept for the ``compression.codex_gpt55_autoraise`` key.
    """
    bare = _codex_route_bare_model(model, provider)
    if bare is None:
        return False
    from agent.model_metadata import is_codex_context_variant
    if is_codex_context_variant(bare):
        return False
    if "astra" in bare:
        return "900k" not in bare
    return bare == "gpt-daybreak-blue-latest" or any(
        bare == fam or bare.startswith(fam + "-") or bare.startswith(fam + ".")
        for fam in ("gpt-5.4", "gpt-5.5", "gpt-5.6"))


def _codex_route_bare_model(model: Optional[str], provider: Optional[str]) -> Optional[str]:
    """Lowercased bare model slug when ``provider`` is the Codex OAuth route, else None."""
    return _bare_model(model) if (provider or "").strip().lower() == "openai-codex" else None


def _is_codex_spark(model: Optional[str], provider: Optional[str] = None) -> bool:
    """True for ``gpt-5.3-codex-spark`` on the Codex OAuth route (the slug exists nowhere else)."""
    return _codex_route_bare_model(model, provider) == "gpt-5.3-codex-spark"


def _fixed_temperature_for_model(
    model: Optional[str], base_url: Optional[str] = None
) -> "Optional[float] | object":
    """``OMIT_TEMPERATURE`` (drop the key; Kimi/Moonshot), a fixed ``float``, or ``None``."""
    if _is_kimi_model(model):
        logger.debug("Omitting temperature for Kimi model %r (server-managed)", model)
        return OMIT_TEMPERATURE
    return 0.5 if _is_arcee_trinity_thinking(model) else None


def _compression_threshold_for_model(
    model: Optional[str], provider: Optional[str] = None, *,
    allow_codex_gpt55_autoraise: bool = True,
) -> Optional[float]:
    """Per-model/route compression threshold override (fraction of context used), or None.

    Arcee Trinity Large Thinking → 0.75 (preserve reasoning context); Codex-route gpt-5.4/5.5/5.6/Astra
    → 0.85, gated by ``allow_codex_gpt55_autoraise``; Codex-route gpt-5.3-codex-spark → 0.70, ungated.
    """
    if _is_arcee_trinity_thinking(model):
        return 0.75
    if allow_codex_gpt55_autoraise and _is_codex_gpt54_or_gpt55(model, provider):
        return _CODEX_GPT54_GPT55_COMPACTION_THRESHOLD
    if _is_codex_spark(model, provider):
        return _CODEX_SPARK_COMPACTION_THRESHOLD
    return None


# Aux "fast tier" families, fastest first (measured p50 titling latency). Matched as substrings
# against the LIVE /v1/models catalog because pinned ids rot; rolling "-latest" aliases lead.
_FAST_MODEL_FAMILIES: tuple = (
    "gpt-mini-latest", "gpt-nano-latest", "claude-haiku-latest", "gemini-flash-latest",
    "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5-mini", "haiku-4.5", "gemini-3.6-flash", "flash-lite",
    "-nano", "-mini", "-flash", "haiku",
)

# Disqualifiers: reasoning variants think before answering; ":batch" is a queue; ":free" tiers
# are rate-limited and slowest; embedders/modality endpoints match a rung but cannot answer.
_FAST_MODEL_EXCLUDE: tuple = (
    "thinking", "reason", "-r1", "minilm", ":batch", ":free",
    "o1-", "o3-", "o4-", "codex", "audio", "-vl", "embed",
    "-tts", "-transcribe", "-realtime", "-image", "-search-preview",
)


def _model_recency_key(model_id: str) -> tuple:
    """Sort key putting a family's newest release first: digit runs compare numerically (plain
    string order picks ``gpt-3.5-mini`` over ``gpt-5.4-mini`` and breaks at 9 vs 10)."""
    # re.split with one capturing group alternates text, number, text, …
    return tuple(
        (1, float(part), "") if index % 2 else (0, 0.0, part)
        for index, part in enumerate(re.split(r"(\d+(?:\.\d+)?)", model_id.lower())) if part)


def _fast_model_from_catalog(provider_id: str) -> str:
    """Newest ``_FAST_MODEL_FAMILIES`` match from the provider's live (cached) catalog.

    "" when the catalog is unavailable or holds no small model (caller falls through to the
    curated default). Never raises; the fetch is memory+disk cached.
    """
    is_nous = provider_id.strip().lower() == "nous"
    try:
        from hermes_cli.auth import resolve_api_key_provider_credentials
        from hermes_cli.models_pricing import fetch_models_with_pricing
        from providers import get_provider_profile
        # Most /v1/models endpoints are authenticated; an anonymous 401 would read as "no small
        # model" and pin the curated default forever.
        api_key, base_url = "", ""
        try:
            creds = resolve_api_key_provider_credentials(provider_id) or {}
            api_key = str(creds.get("api_key", "")).strip()
            base_url = str(creds.get("base_url", "")).strip()
        except Exception:
            # Not an API-key provider, or nothing configured; anonymous fetch may still work.
            logger.debug("No credentials for %s catalog", provider_id, exc_info=True)
        if not api_key and is_nous:
            # Nous is OAuth (resolver raises); anonymous reads return the full catalog.
            try:
                from hermes_cli.models_pricing import _resolve_nous_pricing_credentials
                api_key, base_url = _resolve_nous_pricing_credentials()
            except Exception:
                logger.debug("No Nous credentials for catalog", exc_info=True)
        if not base_url:
            base_url = str(getattr(get_provider_profile(provider_id), "base_url", "") or "")
        base_url = base_url.rstrip("/")
        if not base_url:
            return ""
        if base_url.endswith("/v1"):  # fetch_models_with_pricing appends /v1/models
            base_url = base_url[:-3]
        # Nous-only args must match the pickers' or the seeded cache loses sale chrome and
        # policy-catalog expiry.
        _nous_kwargs = {}
        if is_nous:
            from hermes_cli.models_pricing import _NOUS_CATALOG_TTL_SECONDS
            _nous_kwargs = {"include_sale_original": True, "cache_ttl_seconds": _NOUS_CATALOG_TTL_SECONDS}
        catalog = fetch_models_with_pricing(
            api_key=api_key or None, base_url=base_url, timeout=3.0, **_nous_kwargs) or {}
    except Exception:
        logger.debug("Fast-model catalog lookup failed for %s", provider_id, exc_info=True)
        return ""
    ids = sorted((str(m) for m in catalog), key=_model_recency_key, reverse=True)
    if is_nous:
        # Narrow catalog ids by org policy, as the pickers do.
        try:
            from hermes_cli.models_pricing import nous_policy_allowed_ids, restrict_to_nous_policy
            ids = restrict_to_nous_policy(ids, nous_policy_allowed_ids())
        except Exception:
            logger.debug("Nous policy filter unavailable", exc_info=True)
    for family in _aux._FAST_MODEL_FAMILIES:
        for model_id in ids:
            lowered = model_id.lower()
            if family in lowered and not any(x in lowered for x in _aux._FAST_MODEL_EXCLUDE):
                return model_id
    return ""


# Default auxiliary models for direct API-key providers (cheap/fast for side tasks)
def _get_aux_model_for_provider(provider_id: str, *, prefer_fast: bool = False) -> str:
    """Cheap auxiliary model for a provider.

    Ladder: (``prefer_fast`` only) live-catalog family match, then ``ProviderProfile.resolve_aux_model``;
    then ``default_aux_model`` (curated); then the legacy dict. ``prefer_fast`` is opt-in (titling)
    so other callers keep their static behaviour and cache keys.
    """
    profile = None
    with contextlib.suppress(Exception):
        from providers import get_provider_profile
        profile = get_provider_profile(provider_id)
    picked = ""
    if prefer_fast:
        picked = _aux._fast_model_from_catalog(provider_id)
        if not picked and profile is not None:
            try:
                picked = profile.resolve_aux_model() or ""
            except Exception:
                logger.debug("resolve_aux_model failed for %s", provider_id, exc_info=True)
    if not picked and profile is not None and profile.default_aux_model:
        picked = profile.default_aux_model
    if not picked:
        picked = _API_KEY_PROVIDER_AUX_MODELS_FALLBACK.get(provider_id, "")
    # Rungs 2-4 are policy-blind; a blocked pick is refused at request time, so drop it and
    # let the caller keep the main model.
    if picked and provider_id.strip().lower() == "nous":
        try:
            from hermes_cli.models_pricing import nous_policy_allowed_ids, restrict_to_nous_policy
            allowed = nous_policy_allowed_ids()
            if allowed and not restrict_to_nous_policy([picked], allowed):
                return ""
        except Exception:
            logger.debug("Nous policy check unavailable", exc_info=True)
    return picked


# Fallback for providers without ProviderProfile.default_aux_model (plus some pinned here).
# New providers should set default_aux_model instead.
_API_KEY_PROVIDER_AUX_MODELS_FALLBACK: Dict[str, str] = {
    "gemini": "gemini-3.6-flash", "zai": "glm-4.5-flash", "kimi-coding": "kimi-k2-turbo-preview",
    "stepfun": "step-3.5-flash", "kimi-coding-cn": "kimi-k2-turbo-preview",
    "gmi": "google/gemini-3.1-flash-lite-preview", "anthropic": "claude-haiku-4-5-20251001",
    "ai-gateway": "google/gemini-3-flash", "opencode-zen": "gemini-3-flash", "opencode-go": "glm-5",
    "kilocode": "google/gemini-3.6-flash", "ollama-cloud": "nemotron-3-nano:30b",
    "tencent-tokenhub": "hy4-preview", "tencent-tokenplan": "hy4-preview",
    # No "deepinfra": its aux model lives on the ProviderProfile (read first).
}

# Legacy alias for callers not yet using _get_aux_model_for_provider().
_API_KEY_PROVIDER_AUX_MODELS: Dict[str, str] = _API_KEY_PROVIDER_AUX_MODELS_FALLBACK

# Tasks that may opt into ``auxiliary.<task>.prefer_fast_model``.
_FAST_MODEL_TASKS: frozenset = frozenset({"title_generation"})


def _task_prefers_fast_model(task: Optional[str]) -> bool:
    """Return whether an eligible task explicitly opts into fast-model routing."""
    return task in _FAST_MODEL_TASKS and _aux.is_truthy_value(
        _aux._get_auxiliary_task_config(task).get("prefer_fast_model"), default=False)


# Dedicated vision models for direct providers whose main chat model differs.
_PROVIDER_VISION_MODELS: Dict[str, str] = {"xiaomi": "mimo-v2.5", "zai": "glm-5v-turbo"}


def _resolve_provider_vision_default(provider: str) -> Optional[str]:
    """Provider default vision model id, or None: static ``_PROVIDER_VISION_MODELS`` (vision-only
    names absent from any catalog) win, else ``ProviderProfile.default_vision_model()``."""
    static = _PROVIDER_VISION_MODELS.get(provider)
    if static:
        return static
    try:
        from providers import get_provider_profile
        profile = get_provider_profile(provider)
        return profile.default_vision_model() if profile is not None else None
    except Exception:
        return None


# Endpoints that reject image input: vision auto-detect skips these to the aggregator chain
# instead of returning a client that 404s (Kimi Coding Plan Anthropic wire has no image_in).
_PROVIDERS_WITHOUT_VISION: frozenset = frozenset({"kimi-coding", "kimi-coding-cn"})

# OpenRouter app attribution (always sent). `X-Title` is what the dashboard reads.
_OR_HEADERS_BASE = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
    "X-OpenRouter-Categories": "productivity,cli-agent",
}


def _apply_user_default_headers(headers: dict | None) -> dict | None:
    """Merge user ``model.default_headers`` onto resolved headers (user wins; ``model.extra_headers``
    alias wins over both). Mirrors ``AIAgent._apply_user_default_headers`` so a custom endpoint behind a
    WAF rejecting ``User-Agent`` / ``X-Stainless-*`` works for aux calls. SECURITY: never log values."""
    try:
        from hermes_cli.config import cfg_get, load_config
        _cfg = load_config()
        user_headers = cfg_get(_cfg, "model", "default_headers")
        alias_headers = cfg_get(_cfg, "model", "extra_headers")
        if isinstance(alias_headers, dict) and alias_headers:
            user_headers = {**(user_headers if isinstance(user_headers, dict) else {}), **alias_headers}
    except Exception:
        return headers
    if not isinstance(user_headers, dict) or not user_headers:
        return headers
    merged = dict(headers or {})
    merged.update({str(k): str(v) for k, v in user_headers.items() if v is not None})
    return merged or headers


def build_or_headers(or_config: dict | None = None) -> dict:
    """OpenRouter headers, plus response-cache headers when enabled.

    Precedence env > config > default: ``HERMES_OPENROUTER_CACHE`` overrides
    ``openrouter.response_cache``; ``HERMES_OPENROUTER_CACHE_TTL`` (1-86400 s) overrides
    ``openrouter.response_cache_ttl``. ``or_config=None`` reads from disk.
    """
    headers = dict(_OR_HEADERS_BASE)
    if or_config is None:
        try:
            from hermes_cli.config import load_config_readonly
            or_config = load_config_readonly().get("openrouter", {})
        except Exception:
            or_config = {}
    env_cache = os.environ.get("HERMES_OPENROUTER_CACHE", "").strip().lower()
    if not (env_cache in {"1", "true", "yes", "on"} if env_cache else or_config.get("response_cache", False)):
        return headers
    headers["X-OpenRouter-Cache"] = "true"
    env_ttl = os.environ.get("HERMES_OPENROUTER_CACHE_TTL", "").strip()
    if env_ttl:
        if env_ttl.isdigit() and 1 <= int(env_ttl) <= 86400:
            headers["X-OpenRouter-Cache-TTL"] = str(int(env_ttl))
    else:
        ttl = or_config.get("response_cache_ttl", 300)
        if isinstance(ttl, (int, float)) and 1 <= ttl <= 86400:
            headers["X-OpenRouter-Cache-TTL"] = str(int(ttl))
    return headers


# NVIDIA NIM cloud billing attribution; host-gated because NVIDIA_BASE_URL may be a local NIM.
_NVIDIA_NIM_CLOUD_HEADERS = {"X-BILLING-INVOKE-ORIGIN": "HermesAgent"}


def build_nvidia_nim_headers(base_url: str | None) -> dict:
    """Return NVIDIA NIM cloud attribution headers for build.nvidia.com traffic."""
    return dict(_NVIDIA_NIM_CLOUD_HEADERS) if _aux.base_url_host_matches(str(base_url or ""), "integrate.api.nvidia.com") else {}


# Vercel AI Gateway attribution (HTTP-Referer → referrerUrl, X-Title → appName).
from hermes_cli import __version__ as _HERMES_VERSION

_AI_GATEWAY_HEADERS = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
    "User-Agent": f"HermesAgent/{_HERMES_VERSION}",
}

# Nous Portal attribution extra_body. Tags come from agent.portal_tags so the client= marker
# tracks hermes_cli.__version__ — never inline a literal here.
from agent.portal_tags import nous_portal_tags as _nous_portal_tags


def _nous_extra_body() -> dict:
    """Fresh Nous Portal ``extra_body`` (per call, so a hot-reloaded version is reflected)."""
    return {"tags": _nous_portal_tags()}


# _OPENROUTER_MODEL MUST stay a :free SKU (matching the free_only warning): this lane engages
# silently, and a paid default meant spend the user never opted into. User-configured values
# are honored untouched (_warn_paid_lane_once fires).
_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
_NOUS_MODEL = "google/gemini-3.6-flash"
_NOUS_DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"


# Hosts exposing BOTH ``…/anthropic`` and a sibling OpenAI ``…/v1``. Matched on the URL *host*
# only: unconditional rewrites break Anthropic-only gateways.
_DUAL_SURFACE_ANTHROPIC_HOST_SUFFIXES = ("minimax.io", "minimax.chat", "minimaxi.com")
_DUAL_SURFACE_ANTHROPIC_HOST_PREFIXES = ("api.minimax.",)


def _is_dual_surface_anthropic_host(url: str) -> bool:
    """True when the URL's host is a known dual-surface (MiniMax-family) host."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _DUAL_SURFACE_ANTHROPIC_HOST_SUFFIXES
    ) or any(host.startswith(prefix) for prefix in _DUAL_SURFACE_ANTHROPIC_HOST_PREFIXES)


def _to_openai_base_url(base_url: str) -> str:
    """Normalize dual-surface Anthropic URLs to their OpenAI-compatible sibling.

    MiniMax-family: ``/anthropic`` → ``/v1``; ZAI Coding Plan → ``/coding/paas/v4`` (the general
    endpoint bills separately); Kimi Code ``/coding`` → ``/coding/v1`` (the OpenAI SDK path 404s
    without it). Anthropic-only gateways keep their path.
    """
    url = str(base_url or "").strip().rstrip("/")
    if _aux.base_url_hostname(url) == "api.actual.inc":
        from hermes_cli.auth import normalize_actual_base_url
        return normalize_actual_base_url(url)
    if url.endswith("/anthropic"):
        if _aux.base_url_host_matches(url, "open.bigmodel.cn") or _aux.base_url_host_matches(url, "api.z.ai"):
            rewritten = url[: -len("/anthropic")] + "/coding/paas/v4"
            logger.debug("Auxiliary client: rewrote ZAI base URL %s → %s", url, rewritten)
            return rewritten
        if _is_dual_surface_anthropic_host(url):
            rewritten = url[: -len("/anthropic")] + "/v1"
            logger.debug("Auxiliary client: rewrote dual-surface base URL %s → %s", url, rewritten)
            return rewritten
        logger.debug(
            "Auxiliary client: keeping Anthropic-only base URL %s (no dual-surface host match)", url)
        return url
    if _aux.base_url_host_matches(url, "api.kimi.com") and url.endswith("/coding"):
        rewritten = url + "/v1"
        logger.debug("Auxiliary client: rewrote Kimi base URL %s → %s", url, rewritten)
        return rewritten
    return url


def _load_pool_with_credentials(provider: str, note: str = "") -> Optional[Any]:
    """``load_pool(provider)`` when it has credentials, else None (never raises)."""
    try:
        pool = _aux.load_pool(provider)
    except Exception as exc:
        logger.debug("Auxiliary client: could not load pool for %s%s: %s", provider, note, exc)
        return None
    return pool if pool and pool.has_credentials() else None


def _select_pool_entry(provider: str) -> Tuple[bool, Optional[Any]]:
    """Return (pool_exists_for_provider, selected_entry)."""
    pool = _load_pool_with_credentials(provider)
    if pool is None:
        return False, None
    try:
        return True, pool.select()
    except Exception as exc:
        logger.debug("Auxiliary client: could not select pool entry for %s: %s", provider, exc)
        return True, None


def _peek_pool_entry(provider: str) -> Optional[Any]:
    """Best-effort current/next pool entry without mutating selection order."""
    pool = _load_pool_with_credentials(provider, " (peek)")
    if pool is None:
        return None
    try:
        current_fn = getattr(pool, "current", None)
        current = current_fn() if callable(current_fn) else None
        if current is not None:
            return current
        peek_fn = getattr(pool, "peek", None)
        if callable(peek_fn):
            return peek_fn()
    except Exception as exc:
        logger.debug("Auxiliary client: could not peek pool entry for %s: %s", provider, exc)
    return None


def _pool_runtime_api_key(entry: Any) -> str:
    # runtime_api_key handles provider-specific fallback (e.g. agent_key for nous); None entry → "".
    key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    return str(key or "").strip()


def _pool_runtime_base_url(entry: Any, fallback: str = "") -> str:
    if entry is None:
        return str(fallback or "").strip().rstrip("/")
    if getattr(entry, "provider", None) == "nous":
        # Canonical auth-layer reader so the env override shares one normalization path.
        from hermes_cli.auth import _nous_inference_env_override
        env_url = _nous_inference_env_override()
        if env_url:
            return env_url
    # runtime_base_url is provider-aware; fall back for non-PooledCredential entries.
    url = (getattr(entry, "runtime_base_url", None) or getattr(entry, "inference_base_url", None)
           or getattr(entry, "base_url", None) or fallback)
    return str(url or "").strip().rstrip("/")


# Hosts the aux Anthropic path may be pointed at via model.base_url; anything else falls back
# to the Anthropic default so a foreign host never leaks in.
_ANTHROPIC_COMPATIBLE_HOSTS = frozenset({"api.anthropic.com"})


def _is_anthropic_compatible_host(url: str) -> bool:
    """True for native Anthropic hosts and gateways serving Messages under a ``/anthropic`` path
    (same convention as runtime_provider / ``_wrap_if_needed``), so a configured ``model.base_url``
    whose gateway holds auth is not discarded. A bare non-Anthropic base_url is False."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if (parsed.hostname or "").strip().lower().rstrip(".") in _ANTHROPIC_COMPATIBLE_HOSTS:
            return True
        path = (parsed.path or "").rstrip("/").lower()
        return path.endswith("/anthropic") or path.endswith("/anthropic/v1")
    except Exception:
        return False


def _nous_min_key_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("HERMES_NOUS_MIN_KEY_TTL_SECONDS", "1800")))
    except (TypeError, ValueError):
        return 1800


def _scoped_key_env(name: str) -> str:
    """Read a provider API key (or its paired base-URL) env var through the profile secret scope.

    In agent turns the scope's verdict is authoritative (a scoped miss must not borrow another
    profile's key); unscoped startup/CLI paths fall back to os.environ.
    """
    if not name:
        return ""
    with contextlib.suppress(Exception):
        from agent.secret_scope import UnscopedSecretError, get_secret
        with contextlib.suppress(UnscopedSecretError):
            return (get_secret(name) or "").strip()
    return (os.getenv(name) or "").strip()


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
