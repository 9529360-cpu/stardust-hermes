"""Profile-scoped topic summaries: durable memory that is recalled only when relevant.

Unlike MEMORY.md / USER.md, topic summaries are never part of the system prompt. They
live on disk, are matched locally against the current user turn, and at most a small
bounded subset is injected through the existing per-turn memory-context sidecar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home
from utils import atomic_write_text
from tools.memory_tool_store import MemoryStore, _scan_memory_content

logger = logging.getLogger(__name__)

TOPIC_MEMORY_FILENAME = "TOPICS.json"
_MAX_TOPICS = 64
_MAX_SUMMARY_CHARS = 1600
_MAX_TITLE_CHARS = 80
_MAX_KEYWORDS = 12
_MAX_KEYWORD_CHARS = 48
_DEFAULT_RECALL_LIMIT = 2
_DEFAULT_RECALL_CHAR_BUDGET = 1800
_MAX_SOURCE_CHARS = 160
# Provenance fields added by DL-P2.7; stripped from recall rows when the flag is
# OFF so flag-off output stays byte-identical even over a store written flag-on.
_PROVENANCE_FIELDS = frozenset({"source", "observed_at", "valid_until", "verified", "superseded_on"})
# ISO-8601 prefix (date or datetime, optionally with time zone) for provenance stamps.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def topic_memory_path() -> Path:
    return get_hermes_home() / "memories" / TOPIC_MEMORY_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_timestamp(value: Any) -> str | None:
    """Return a valid ISO-8601 timestamp string, or None for empty/invalid input."""
    text = str(value or "").strip()
    if not text:
        return None
    if not _TIMESTAMP_RE.match(text):
        return None
    return text


def _parse_timestamp(value: Any) -> datetime:
    """Parse an ISO-8601 date/datetime; naive values are treated as UTC."""
    text = str(value or "").strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().lower()


def _topic_key(value: Any) -> str:
    text = _norm(value)
    text = re.sub(r"[^\w\u3400-\u9fff]+", "-", text, flags=re.UNICODE).strip("-_")
    return text[:80]


def _keywords(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [piece.strip() for piece in re.split(r"[,，;；\n]+", values)]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = unicodedata.normalize("NFKC", str(value or "")).strip()
        folded = item.lower()
        if not item or folded in seen:
            continue
        seen.add(folded)
        out.append(item[:_MAX_KEYWORD_CHARS])
        if len(out) >= _MAX_KEYWORDS:
            break
    return out


def _read_payload(*, strict: bool) -> dict[str, Any]:
    path = topic_memory_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": 1, "topics": []}
    except OSError:
        if strict:
            raise
        logger.warning("Could not read topic summaries", exc_info=True)
        return {"version": 1, "topics": []}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if strict:
            raise RuntimeError(f"{TOPIC_MEMORY_FILENAME} is not valid JSON; refusing to overwrite it")
        logger.warning("Ignoring malformed %s during recall", TOPIC_MEMORY_FILENAME)
        return {"version": 1, "topics": []}
    topics = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(topics, list):
        if strict:
            raise RuntimeError(f"{TOPIC_MEMORY_FILENAME} has an invalid topics payload; refusing to overwrite it")
        return {"version": 1, "topics": []}
    return {"version": 1, "topics": [item for item in topics if isinstance(item, dict)]}


def load_topics() -> list[dict[str, Any]]:
    return list(_read_payload(strict=False)["topics"])


def _write_payload(payload: dict[str, Any]) -> None:
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        tmp_prefix=".topics.",
        preserve_mode=True,
        create_mode=0o600,
        fsync_dir=True,
    )


def _topic_version(topic: dict[str, Any]) -> str:
    material = "\0".join(
        [
            str(topic.get("key") or ""),
            str(topic.get("summary") or ""),
            "\0".join(str(x) for x in topic.get("keywords") or []),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def list_topic_summaries(*, include_summary: bool = False) -> list[dict[str, Any]]:
    result = []
    for topic in load_topics():
        row = {
            "key": str(topic.get("key") or ""),
            "title": str(topic.get("title") or ""),
            "keywords": list(topic.get("keywords") or []),
            "updated_at": topic.get("updated_at"),
            "version": _topic_version(topic),
        }
        if include_summary:
            row["summary"] = str(topic.get("summary") or "")
        result.append(row)
    return result


def get_topic_summary(topic: str) -> dict[str, Any] | None:
    key = _topic_key(topic)
    for row in load_topics():
        if _topic_key(row.get("key") or row.get("title")) == key:
            return {**row, "version": _topic_version(row)}
    return None


def upsert_topic_summary(
    topic: str,
    summary: str,
    *,
    title: str | None = None,
    keywords: Any = None,
    source: str | None = None,
    observed_at: str | None = None,
    valid_until: str | None = None,
    verified: bool | None = None,
) -> dict[str, Any]:
    key = _topic_key(topic or title)
    clean_summary = str(summary or "").strip()
    clean_title = str(title or topic or "").strip()[:_MAX_TITLE_CHARS]
    clean_keywords = _keywords(keywords)
    if not key:
        return {"success": False, "error": "topic is required for a topic summary"}
    if not clean_summary:
        return {"success": False, "error": "content is required for a topic summary"}
    clean_source = str(source or "").strip()[:_MAX_SOURCE_CHARS] or None
    clean_observed_at = _normalize_timestamp(observed_at)
    clean_valid_until = _normalize_timestamp(valid_until)
    stamps = {"observed_at": (observed_at, clean_observed_at), "valid_until": (valid_until, clean_valid_until)}
    for label, (raw, normalized) in stamps.items():
        if str(raw or "").strip() and normalized is None:
            return {"success": False, "error": f"invalid {label}: use ISO 8601 (e.g. 2026-10-24 or 2026-10-24T09:00:00+02:00)"}
    if (clean_observed_at and clean_valid_until
            and _parse_timestamp(clean_valid_until) < _parse_timestamp(clean_observed_at)):
        return {"success": False, "error": "valid_until is earlier than observed_at"}
    if len(clean_summary) > _MAX_SUMMARY_CHARS:
        return {
            "success": False,
            "error": f"Topic summary is too long ({len(clean_summary)}/{_MAX_SUMMARY_CHARS} chars). Condense it first.",
        }
    threat = _scan_memory_content("\n".join([clean_title, *clean_keywords, clean_summary]))
    if threat:
        return {"success": False, "error": threat}

    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        topics = list(payload["topics"])
        idx = next(
            (i for i, row in enumerate(topics) if _topic_key(row.get("key") or row.get("title")) == key),
            None,
        )
        now = _now()
        existing = topics[idx] if idx is not None else {}
        row = {
            "key": key,
            "title": clean_title or str(existing.get("title") or topic).strip()[:_MAX_TITLE_CHARS],
            "summary": clean_summary,
            "keywords": clean_keywords or list(existing.get("keywords") or []),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        if clean_source is not None:
            row["source"] = clean_source
        elif isinstance(existing.get("source"), str) and existing["source"].strip():
            row["source"] = existing["source"]
        if clean_observed_at is not None:
            row["observed_at"] = clean_observed_at
        elif isinstance(existing.get("observed_at"), str) and existing["observed_at"].strip():
            row["observed_at"] = existing["observed_at"]
        if clean_valid_until is not None:
            row["valid_until"] = clean_valid_until
        elif isinstance(existing.get("valid_until"), str) and existing["valid_until"].strip():
            row["valid_until"] = existing["valid_until"]
        if verified is not None:
            row["verified"] = bool(verified)
        elif isinstance(existing.get("verified"), bool):
            row["verified"] = existing["verified"]
        if idx is None:
            if len(topics) >= _MAX_TOPICS:
                return {
                    "success": False,
                    "error": f"Topic memory already has {_MAX_TOPICS} topics. Consolidate or remove a stale topic first.",
                }
            topics.append(row)
            change = "created"
        else:
            topics[idx] = row
            change = "updated"
        _write_payload({"version": 1, "topics": topics})
    return {
        "success": True,
        "target": "topic",
        "topic": key,
        "title": row["title"],
        "message": f"Topic summary {change}",
        "version": _topic_version(row),
    }


def reset_topic_summaries() -> bool:
    """Delete the local topic-summary store. Returns whether the file existed."""
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        existed = path.exists()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Atomic empty replacement is safer than leaving stale recallable data behind.
            atomic_write_text(path, '{"version": 1, "topics": []}\n', create_mode=0o600, fsync_dir=True)
        return existed


def remove_topic_summary(topic: str) -> dict[str, Any]:
    key = _topic_key(topic)
    if not key:
        return {"success": False, "error": "topic is required"}
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        topics = list(payload["topics"])
        kept = [row for row in topics if _topic_key(row.get("key") or row.get("title")) != key]
        if len(kept) == len(topics):
            return {"success": False, "error": f"Topic '{topic}' was not found"}
        _write_payload({"version": 1, "topics": kept})
    return {"success": True, "target": "topic", "topic": key, "message": "Topic summary removed"}


def _signals(text: str) -> set[str]:
    text = _norm(text)
    signals = {token for token in re.findall(r"[a-z0-9][a-z0-9._-]+", text) if len(token) >= 2}
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        if len(segment) == 1:
            signals.add(segment)
        else:
            signals.update(segment[i : i + 2] for i in range(len(segment) - 1))
    return signals


def _score(query: str, query_signals: set[str], topic: dict[str, Any]) -> int:
    q = _norm(query)
    title = _norm(topic.get("title"))
    key = _norm(topic.get("key"))
    keywords = [_norm(item) for item in topic.get("keywords") or [] if _norm(item)]
    score = 0
    if title and len(title) >= 2 and title in q:
        score += 20
    if key and len(key) >= 2 and key in q:
        score += 16
    for keyword in keywords:
        if len(keyword) >= 2 and keyword in q:
            score += 12
    candidate_signals = _signals(" ".join([title, key, *keywords]))
    score += 3 * len(query_signals & candidate_signals)
    return score


def _expired(topic: dict[str, Any], now: datetime) -> bool:
    """True when the topic carries a valid_until that has passed."""
    valid_until = topic.get("valid_until")
    if not isinstance(valid_until, str) or not valid_until.strip():
        return False
    return _parse_timestamp(valid_until) < now


def _observed_sort_key(topic: dict[str, Any]) -> tuple[int, str]:
    """Newest observation first; missing observed_at sorts last (fallback: updated_at)."""
    raw = topic.get("observed_at") or topic.get("updated_at") or ""
    parsed = _parse_timestamp(raw) if str(raw or "").strip() else None
    if parsed is None or parsed == datetime.min.replace(tzinfo=timezone.utc):
        return (0, "")
    return (1, str(parsed))


def _same_domain_forks(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two topics are same-domain forks: equal normalized keys, one key
    a dash-separated prefix of the other, or >= 2 normalized keywords shared."""
    ka, kb = _topic_key(a.get("key") or ""), _topic_key(b.get("key") or "")
    if ka and kb:
        if ka == kb:
            return True
        if kb.startswith(ka + "-") or ka.startswith(kb + "-"):
            return True
    kwa = {_norm(k) for k in a.get("keywords") or [] if _norm(k)}
    kwb = {_norm(k) for k in b.get("keywords") or [] if _norm(k)}
    return len(kwa & kwb) >= 2


def _rank_superseded_forks(scored: list[tuple[int, dict[str, Any]]]) -> dict[str, str]:
    """Within each same-domain fork pair, mark the older topic superseded by the newer.

    Returns {superseded_key: supersede_date} where supersede_date is the newer
    topic's observed_at (or updated_at), used both for ranking (superseded rows
    drop below every current row) and for the injected label. Both-legs marking
    keeps the pair from being injected as two competing current facts; a topic
    with no newer same-domain partner is never marked.
    """
    superseded: dict[str, str] = {}
    n = len(scored)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ti, tj = scored[i][1], scored[j][1]
            if not _same_domain_forks(ti, tj):
                continue
            if _observed_sort_key(tj) <= _observed_sort_key(ti):
                continue
            # j is strictly newer than i in the same domain: i is superseded.
            superseded[str(ti.get("key") or "")] = _display_date(tj)
    return superseded


def _display_date(topic: dict[str, Any]) -> str:
    raw = str(topic.get("observed_at") or topic.get("updated_at") or "").strip()
    return raw[:10] if raw else "unknown date"


def recall_topic_summaries(
    query: str,
    *,
    limit: int = _DEFAULT_RECALL_LIMIT,
    char_budget: int = _DEFAULT_RECALL_CHAR_BUDGET,
    provenance: bool = False,
) -> list[dict[str, Any]]:
    clean_query = str(query or "").strip()
    if not clean_query or limit <= 0 or char_budget <= 0:
        return []
    query_signals = _signals(clean_query)
    now = datetime.now(timezone.utc)
    scored = []
    for topic in load_topics():
        if provenance and _expired(topic, now):
            continue
        score = _score(clean_query, query_signals, topic)
        if score >= 6 and str(topic.get("summary") or "").strip():
            scored.append((score, topic))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("key") or "")))
    superseded: dict[str, str] = {}
    if provenance and scored:
        superseded = _rank_superseded_forks(scored)
        if superseded:
            fresh_rows = [item for item in scored if str(item[1].get("key") or "") not in superseded]
            stale_rows = [item for item in scored if str(item[1].get("key") or "") in superseded]
            scored = fresh_rows + stale_rows

    selected: list[dict[str, Any]] = []
    used = 0
    for score, topic in scored:
        summary = str(topic.get("summary") or "").strip()
        title = str(topic.get("title") or topic.get("key") or "").strip()
        superseded_by = superseded.get(str(topic.get("key") or ""))
        cost = len(title) + len(summary) + 16
        if selected and used + cost > char_budget:
            continue
        if not selected and cost > char_budget:
            summary = summary[: max(0, char_budget - len(title) - 32)].rstrip()
            cost = len(title) + len(summary) + 16
        if not summary:
            continue
        selected.append(
            {
                **({k: v for k, v in topic.items() if k not in _PROVENANCE_FIELDS} if not provenance else topic),
                "summary": summary,
                "score": score,
                "version": _topic_version(topic),
                **({"superseded_on": superseded_by} if superseded_by is not None else {}),
            }
        )
        used += cost
        if len(selected) >= limit:
            break
    return selected


def _provenance_note(topic: dict[str, Any]) -> str:
    """Compact evidence/uncertainty suffix for one injected topic row."""
    bits: list[str] = []
    if topic.get("superseded_on"):
        bits.append(f"older, superseded on {topic['superseded_on']}")
    observed = str(topic.get("observed_at") or "").strip()
    if observed:
        bits.append(f"observed {observed[:10]}")
    if topic.get("valid_until"):
        bits.append(f"valid until {str(topic['valid_until'])[:10]}")
    if topic.get("verified") is True:
        bits.append("verified")
    elif topic.get("verified") is False:
        bits.append("unverified")
    if topic.get("source"):
        bits.append(f"source: {topic['source']}")
    return f" ({'; '.join(bits)})" if bits else ""


def format_topic_recall(topics: Iterable[dict[str, Any]]) -> str:
    rows = list(topics)
    if not rows:
        return ""
    parts = [
        "Relevant topic summaries (current state; use session_search only when exact historical detail is needed):"
    ]
    for topic in rows:
        note = _provenance_note(topic)
        header = f"### {topic.get('title') or topic.get('key')}"
        body = topic.get("summary") or ""
        if note:
            # Own line under the header: the bare `### <title>` line stays parseable
            # by consumers that map titles back to topic keys.
            parts.append(f"{header}\n{note}\n{body}")
        else:
            parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def review_topic_context(messages: Iterable[dict[str, Any]], *, limit: int = 3, char_budget: int = 2400) -> str:
    inventory = list_topic_summaries()
    if not inventory:
        return ""
    text_parts: list[str] = []
    for message in list(messages)[-18:]:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            text_parts.append(content[-1200:])
    selected = recall_topic_summaries("\n".join(text_parts), limit=limit, char_budget=char_budget)
    keys = ", ".join(
        f"{row.get('key')} ({row.get('title')})" if row.get("title") else str(row.get("key"))
        for row in inventory[:24]
    )
    lines = [
        "Existing topic keys (reuse these instead of creating near-duplicates): " + keys + ".",
        "If the conversation changes an existing topic, UPDATE the same topic key; preserve still-current "
        "facts and remove superseded ones instead of appending chronology.",
    ]
    if selected:
        lines.append("Relevant current summaries:")
    for topic in selected:
        keywords = ", ".join(str(x) for x in topic.get("keywords") or [])
        lines.append(
            f"- topic={topic.get('key')} | title={topic.get('title')} | keywords={keywords}\n"
            f"  {topic.get('summary')}"
        )
    return "\n".join(lines)
