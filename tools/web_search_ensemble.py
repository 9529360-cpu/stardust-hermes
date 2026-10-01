"""Opt-in multi-provider web search with URL dedupe and Reciprocal Rank Fusion.

The normal single-provider path remains the default. When web.search_ensemble_backends is
configured, Stardust queries the primary plus up to five explicit additional providers in
parallel, merges duplicate URLs, and reranks by RRF. Provider calls use their normal per-provider
search cache, while the fused result gets its own cache key.
"""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

logger = logging.getLogger("tools.web_tools")

_RRF_K = 60.0
_MAX_EXTRA_BACKENDS = 5
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref_src",
})


def configured_ensemble_backends(primary_name: str = "") -> list[str]:
    """Ordered explicit additional search backends, normalized and bounded for cost safety."""
    try:
        from tools.web_tools import _load_web_config
        raw = _load_web_config().get("search_ensemble_backends")
    except Exception as exc:
        logger.debug("search ensemble config read failed: %s", exc)
        return []
    raw = [raw] if isinstance(raw, str) else raw
    if not isinstance(raw, (list, tuple)):
        return []
    primary = str(primary_name or "").strip().lower()
    out: list[str] = []
    for item in raw:
        name = str(item or "").strip().lower()
        if not name or name == primary or name in out:
            continue
        out.append(name)
    if len(out) > _MAX_EXTRA_BACKENDS:
        logger.warning(
            "web.search_ensemble_backends has %d entries; using first %d to bound parallel cost",
            len(out), _MAX_EXTRA_BACKENDS,
        )
        out = out[:_MAX_EXTRA_BACKENDS]
    return out


def _provider_ready(provider) -> bool:
    if provider is None:
        return False
    try:
        if not provider.supports_search():
            return False
        if provider.is_available():
            return True
        return bool(provider.is_keyless_available())
    except Exception:
        return False


def _provider_error(response) -> str:
    if not isinstance(response, dict):
        return "malformed response"
    if not response.get("success"):
        return str(response.get("error") or "search failed")
    web = (response.get("data") or {}).get("web")
    return "" if isinstance(web, list) else "success response missing data.web list"


def _direct_cached_search(provider, query: str, limit: int) -> dict:
    """Direct provider call with normal memoization but no rescue/fallback recursion."""
    from tools.web_result_cache import bucket_limit, search_memo, slice_search_response

    cached = search_memo.lookup(provider.name, query, limit)
    if cached is not None:
        return slice_search_response(cached, limit)

    with search_memo.flight_lock(provider.name, query, limit):
        cached = search_memo.lookup(provider.name, query, limit)
        if cached is not None:
            return slice_search_response(cached, limit)
        response = provider.search(query, bucket_limit(limit))
        if isinstance(response, dict) and response.get("success"):
            search_memo.store(provider.name, query, limit, response)
        return slice_search_response(response, limit) if isinstance(response, dict) else {
            "success": False, "error": "malformed response"
        }


def _canonical_url(url: str) -> str:
    """Stable dedupe key: ignore scheme/fragment/common tracking params, preserve real query semantics."""
    try:
        parsed = urlsplit(str(url or "").strip())
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        filtered = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lower = key.lower()
            if lower.startswith("utm_") or lower in _TRACKING_QUERY_KEYS:
                continue
            filtered.append((key, value))
        query = urlencode(sorted(filtered))
        return f"{host}{port}{path}" + (f"?{query}" if query else "")
    except Exception:
        return str(url or "").strip().lower()


def _rows(response: dict) -> list[dict]:
    web = (response.get("data") or {}).get("web") if isinstance(response, dict) else None
    return [row for row in web if isinstance(row, dict)] if isinstance(web, list) else []


def _merge_rrf(provider_results: list[tuple[str, dict]], limit: int) -> list[dict]:
    """Merge provider rankings with RRF and canonical-URL dedupe."""
    merged: dict[str, dict] = {}
    order = 0
    for provider_name, response in provider_results:
        for rank, row in enumerate(_rows(response), start=1):
            raw_url = str(row.get("url") or "").strip()
            key = _canonical_url(raw_url)
            if not key:
                key = f"__no_url__:{provider_name}:{rank}:{row.get('title', '')}"
            item = merged.get(key)
            if item is None:
                item = merged[key] = {
                    "score": 0.0,
                    "first_seen": order,
                    "title": str(row.get("title") or ""),
                    "url": raw_url,
                    "description": str(row.get("description") or ""),
                    "sources": [],
                }
                order += 1
            item["score"] += 1.0 / (_RRF_K + rank)
            if provider_name not in item["sources"]:
                item["sources"].append(provider_name)
            title = str(row.get("title") or "")
            desc = str(row.get("description") or "")
            if not item["title"] and title:
                item["title"] = title
            if len(desc) > len(item["description"]):
                item["description"] = desc
            if not item["url"] and raw_url:
                item["url"] = raw_url

    ranked = sorted(merged.values(), key=lambda item: (-item["score"], item["first_seen"]))
    out = []
    for position, item in enumerate(ranked[:limit], start=1):
        out.append({
            "title": item["title"],
            "url": item["url"],
            "description": item["description"],
            "position": position,
            "sources": item["sources"],
        })
    return out


def _resolve_providers(primary_provider) -> tuple[list, dict[str, str]]:
    """Primary plus available explicit extras; unresolved extras are reported, not fatal."""
    extras = configured_ensemble_backends(getattr(primary_provider, "name", ""))
    if not extras:
        return [primary_provider], {}

    from agent.web_search_registry import get_provider

    providers = [primary_provider]
    failures: dict[str, str] = {}
    for name in extras:
        provider = get_provider(name)
        if provider is None:
            failures[name] = "not registered"
            continue
        if not _provider_ready(provider):
            failures[name] = "unavailable"
            continue
        providers.append(provider)
    return providers, failures


def search_ensemble(primary_provider, query: str, limit: int) -> Optional[dict]:
    """Run the opt-in ensemble; None means no usable additional provider was configured."""
    providers, resolution_failures = _resolve_providers(primary_provider)
    if len(providers) < 2:
        return None

    from tools.web_result_cache import search_memo, slice_search_response

    names = [str(provider.name) for provider in providers]
    cache_key = "ensemble:" + ",".join(names)
    cached = search_memo.lookup(cache_key, query, limit)
    if cached is not None:
        return slice_search_response(cached, limit)

    with search_memo.flight_lock(cache_key, query, limit):
        cached = search_memo.lookup(cache_key, query, limit)
        if cached is not None:
            return slice_search_response(cached, limit)

        successes: dict[str, dict] = {}
        failures = dict(resolution_failures)
        with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="web-ensemble") as pool:
            futures = {}
            for provider in providers:
                ctx = contextvars.copy_context()
                future = pool.submit(ctx.run, _direct_cached_search, provider, query, limit)
                futures[future] = str(provider.name)
            for future in as_completed(futures):
                name = futures[future]
                try:
                    response = future.result()
                except Exception as exc:
                    failures[name] = f"{type(exc).__name__}: {str(exc)[:240]}"
                    continue
                error = _provider_error(response)
                if error:
                    failures[name] = error[:300]
                else:
                    successes[name] = response

        if not successes:
            detail = "; ".join(f"{name}: {error}" for name, error in sorted(failures.items()))
            return {
                "success": False,
                "error": "All configured ensemble search providers failed"
                + (f" ({detail})" if detail else ""),
            }

        ordered_successes = [(name, successes[name]) for name in names if name in successes]
        response = {
            "success": True,
            "data": {
                "web": _merge_rrf(ordered_successes, limit),
                "search_mode": "ensemble",
                "backends": [name for name, _ in ordered_successes],
            },
        }
        if failures:
            response["data"]["failed_backends"] = failures
        search_memo.store(cache_key, query, limit, response)
        return response
