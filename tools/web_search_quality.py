"""Post-fusion quality helpers for adaptive web search.

These helpers use only evidence already present in search results. They do not maintain a domain
allowlist or invent an authority score. Undated results are not penalized; freshness is a bounded
bonus only when a provider supplied a parseable publication/update date.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any
from urllib.parse import urlsplit


_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]+")
_DATE_KEYS = (
    "published_date",
    "published_at",
    "publishedAt",
    "date",
    "updated_at",
    "updatedAt",
)


def _host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _normalized_title(title: str) -> str:
    return " ".join(token.lower() for token in _TITLE_TOKEN_RE.findall(str(title or "")))


def _parse_date(value: Any) -> _dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, _dt.date):
        dt = _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = _dt.datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def publication_date(row: dict[str, Any]) -> _dt.datetime | None:
    for key in _DATE_KEYS:
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _freshness_multiplier(row: dict[str, Any], now: _dt.datetime) -> float:
    published = publication_date(row)
    if published is None:
        return 1.0
    age_days = max(0.0, (now - published).total_seconds() / 86400.0)
    if age_days <= 2:
        return 1.16
    if age_days <= 7:
        return 1.12
    if age_days <= 30:
        return 1.08
    if age_days <= 180:
        return 1.03
    return 1.0


def _merge_exact_title_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only strong near-duplicates: exact normalized long titles.

    A short generic title such as "Home" or "Documentation" is not enough evidence. When two
    copies collapse, keep the higher-scored row and union their source lists.
    """
    out: list[dict[str, Any]] = []
    by_title: dict[str, dict[str, Any]] = {}
    for item in items:
        title_key = _normalized_title(item.get("title", ""))
        strong = len(title_key) >= 24 or len(title_key.split()) >= 5
        if not title_key or not strong:
            out.append(item)
            continue
        prior = by_title.get(title_key)
        if prior is None:
            by_title[title_key] = item
            out.append(item)
            continue
        prior_sources = prior.setdefault("sources", [])
        for source in item.get("sources", []):
            if source not in prior_sources:
                prior_sources.append(source)
        if float(item.get("_quality_score", item.get("score", 0.0))) > float(
            prior.get("_quality_score", prior.get("score", 0.0))
        ):
            # Preserve list position but replace the representative payload.
            idx = out.index(prior)
            merged_sources = list(prior_sources)
            replacement = dict(item)
            replacement["sources"] = list(dict.fromkeys([*merged_sources, *item.get("sources", [])]))
            out[idx] = replacement
            by_title[title_key] = replacement
    return out


def _diverse_take(items: list[dict[str, Any]], limit: int, per_host: int = 2) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    host_counts: dict[str, int] = {}
    for item in items:
        host = _host(item.get("url", ""))
        if host and host_counts.get(host, 0) >= per_host:
            deferred.append(item)
            continue
        chosen.append(item)
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
        if len(chosen) >= limit:
            return chosen
    for item in deferred:
        if len(chosen) >= limit:
            break
        chosen.append(item)
    return chosen


def rerank_fused_results(
    items: list[dict[str, Any]],
    limit: int,
    *,
    freshness: bool = False,
    diversity: bool = False,
    near_duplicate_dedupe: bool = False,
    now: _dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply bounded evidence-based quality signals after RRF."""
    current = now or _dt.datetime.now(_dt.timezone.utc)
    prepared: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        base = float(row.get("score", 0.0))
        row["_quality_score"] = base * (_freshness_multiplier(row, current) if freshness else 1.0)
        prepared.append(row)

    prepared.sort(
        key=lambda row: (
            -float(row.get("_quality_score", 0.0)),
            int(row.get("first_seen", 0)),
        )
    )
    if near_duplicate_dedupe:
        prepared = _merge_exact_title_duplicates(prepared)
    selected = _diverse_take(prepared, limit) if diversity else prepared[:limit]

    out: list[dict[str, Any]] = []
    for position, item in enumerate(selected[:limit], start=1):
        row = {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
            "position": position,
            "sources": list(item.get("sources", [])),
        }
        for key in _DATE_KEYS:
            if item.get(key) is not None:
                row[key] = item[key]
                break
        out.append(row)
    return out
