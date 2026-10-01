"""One-shot keyless-ring rescue for failed keyed/configured web calls.

Stateless by design: a rescue routes THIS call through the free-tier ring (plugins/web/keyless_mcp.py);
the next web_search/web_extract call attempts the chosen backend again. Callers must never cache a
rescue-served response, or the one-shot rescue becomes sticky for a whole TTL. Logs under the origin
(tools.web_tools) logger.
"""

import logging

logger = logging.getLogger("tools.web_tools")

# Ring vendor -> env var holding its paid key (keyed mode ⇒ eligible for rescue).
_RING_KEY_VARS = {
    "exa": "EXA_API_KEY", "parallel": "PARALLEL_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY", "keenable": "KEENABLE_API_KEY",
}


def _configured_search_fallbacks() -> list[str]:
    """Explicit one-shot search fallback chain from web.search_fallback_backends.

    A string is accepted as a one-item list for hand-edited configs. Names are normalized,
    blanks/duplicates are removed, and no provider is contacted here.
    """
    try:
        from tools.web_tools import _load_web_config
        raw = _load_web_config().get("search_fallback_backends")
    except Exception as exc:  # noqa: BLE001 — config is best-effort on rescue path
        logger.debug("search fallback config read failed: %s", exc)
        return []
    raw = [raw] if isinstance(raw, str) else raw
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        name = str(item or "").strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def _configured_fallback_search(provider_name: str, original_error: str, query: str, limit: int):
    """Try explicitly configured fallback providers in order.

    Returns (result_or_none, failures). A fallback call never recursively triggers rescue:
    the chain is bounded by the configured list, and keyless-ring rescue remains the final step.
    """
    fallbacks = [name for name in _configured_search_fallbacks() if name != provider_name]
    if not fallbacks:
        return None, []
    try:
        from agent.web_search_registry import get_provider
    except Exception as exc:  # noqa: BLE001
        return None, [f"registry unavailable: {exc}"]

    failures = []
    for name in fallbacks:
        provider = get_provider(name)
        if provider is None:
            failures.append(f"{name}: not registered")
            continue
        try:
            if not provider.supports_search():
                failures.append(f"{name}: search unsupported")
                continue
            keyed = bool(provider.is_available())
            keyless = False if keyed else bool(provider.is_keyless_available())
            if not keyed and not keyless:
                failures.append(f"{name}: unavailable")
                continue
            logger.warning(
                "web_search backend '%s' failed (%s); trying configured fallback '%s'",
                provider_name, (original_error or "")[:200], name,
            )
            result = provider.search(query, limit)
        except Exception as exc:  # noqa: BLE001 — continue down the explicit chain
            failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        if not isinstance(result, dict) or not result.get("success"):
            failures.append(f"{name}: {str((result or {}).get('error', 'search failed'))[:180]}")
            continue
        data = result.setdefault("data", {})
        if isinstance(data, dict):
            data.update(
                fallback_from=provider_name,
                fallback_backend=name,
                backend_error=(
                    f"Configured backend '{provider_name}' failed this call "
                    f"({(original_error or 'unknown error')[:300]}); result served by "
                    f"configured fallback '{name}'. The next call will retry '{provider_name}'."
                ),
            )
        return result, failures
    return None, failures


def _keyless_rescue_enabled() -> bool:
    """``web.keyless_rescue`` (default on), implicitly off when the keyless tier is disabled."""
    from tools.web_tools import _load_web_config
    if not _load_web_config().get("keyless_rescue", True):
        return False
    try:
        from agent.web_search_registry import _keyless_tier_enabled
        return _keyless_tier_enabled()
    except Exception as exc:  # noqa: BLE001 — registry optional
        logger.debug("keyless rescue tier check failed: %s", exc)
        return False


def _rescue_eligible(provider) -> bool:
    """True when a failed call has an explicit fallback or keyless rescue path.

    Explicit search_fallback_backends are user-authorized and remain available even when the
    anonymous keyless tier is disabled. Otherwise keep the historical rule: keyed/configured
    paths may use keyless rescue, while a ring vendor already in keyless mode must not double-walk.
    """
    if provider is None:
        return False
    name = str(getattr(provider, "name", "") or "").strip().lower()
    if any(candidate != name for candidate in _configured_search_fallbacks()):
        return True
    if not _keyless_rescue_enabled():
        return False
    try:
        from plugins.web.keyless_mcp import _KEYLESS_RING, use_keyless
        if name not in _KEYLESS_RING:
            return True
        from agent.web_search_provider import get_provider_env
        key_var = _RING_KEY_VARS.get(name, "")
        return not use_keyless(name, get_provider_env(key_var) if key_var else "")
    except Exception as exc:  # noqa: BLE001 — rescue is best-effort
        logger.debug("rescue eligibility check failed: %s", exc)
        return False


def _rescue_search(provider_name: str, original_error: str, query: str, limit: int) -> dict:
    """Rescue a failed search through explicit fallbacks, then the anonymous keyless ring."""
    explicit, fallback_failures = _configured_fallback_search(
        provider_name, original_error, query, limit
    )
    if explicit is not None:
        return explicit

    if not _keyless_rescue_enabled():
        suffix = (
            f" (configured fallbacks also failed: {'; '.join(fallback_failures)})"
            if fallback_failures else ""
        )
        return {"success": False, "error": f"{original_error or 'search failed'}{suffix}"}

    from plugins.web.keyless_mcp import search_with_failover
    logger.warning(
        "web_search backend '%s' failed (%s); one-shot keyless rescue",
        provider_name, (original_error or "")[:200],
    )
    rescued = search_with_failover(provider_name, query, limit)
    if rescued.get("success"):
        rescued.setdefault("data", {}).update(
            rescued_from=provider_name,
            backend_error=(
                f"Configured backend '{provider_name}' failed this call "
                f"({(original_error or 'unknown error')[:300]}); result served by the keyless free tier. "
                f"The next call will use '{provider_name}' again."
            ),
        )
        if fallback_failures:
            rescued["data"]["fallback_errors"] = fallback_failures[:8]
        return rescued
    # Ring also failed: the ORIGINAL error names the user's setup, so lead with it.
    detail = f"; configured fallbacks: {'; '.join(fallback_failures)}" if fallback_failures else ""
    return {
        "success": False,
        "error": (
            f"{original_error or 'search failed'} "
            f"(keyless rescue also failed: {rescued.get('error', 'unknown')}{detail})"
        ),
    }


def _policy_blocked_result(result: dict) -> bool:
    """True for a website-policy refusal — intentional, never rescued (it would fetch blocked content)."""
    error = str(result.get("error") or "").lower()
    return bool(result.get("blocked_by_policy")) or "blocked by website policy" in error


def _rescue_extract(provider_name: str, urls: list, results: list) -> list:
    """Rescue a whole-batch extract failure via the ring.

    Only genuine failures are re-fetched; policy-blocked entries are preserved verbatim. If the provider
    broke url/result order parity, every entry is treated as rescueable and the ring's list replaces the
    batch wholesale.
    """
    from plugins.web.keyless_mcp import extract_with_failover

    parity = len(results) == len(urls)
    rescue_idx = [i for i, r in enumerate(results) if not parity or not _policy_blocked_result(r)]
    if not rescue_idx:
        return results  # every failure is an intentional policy block

    rescue_urls = [urls[i] for i in rescue_idx] if parity else list(urls)
    errors = (results[i].get("error") for i in rescue_idx if results[i].get("error"))
    original_error = next(errors, "extract failed")
    logger.warning(
        "web_extract backend '%s' failed all %d URL(s) (%s); one-shot keyless rescue",
        provider_name, len(rescue_urls), (original_error or "")[:200],
    )
    rescued = extract_with_failover(provider_name, list(rescue_urls))
    if rescued and all(r.get("error", "") for r in rescued):
        return results  # rescue also failed everywhere: keep original errors
    for r in rescued:
        meta = None if r.get("error") else r.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["rescued_from"] = provider_name
            meta["backend_error"] = (original_error or "")[:300]
    if parity and len(rescued) == len(rescue_idx):
        replacements = dict(zip(rescue_idx, rescued))
        return [replacements.get(i, r) for i, r in enumerate(results)]
    return rescued
