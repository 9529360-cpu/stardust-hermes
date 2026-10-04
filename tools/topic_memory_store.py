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


def topic_memory_path() -> Path:
    return get_hermes_home() / "memories" / TOPIC_MEMORY_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
) -> dict[str, Any]:
    key = _topic_key(topic or title)
    clean_summary = str(summary or "").strip()
    clean_title = str(title or topic or "").strip()[:_MAX_TITLE_CHARS]
    clean_keywords = _keywords(keywords)
    if not key:
        return {"success": False, "error": "topic is required for a topic summary"}
    if not clean_summary:
        return {"success": False, "error": "content is required for a topic summary"}
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


def recall_topic_summaries(
    query: str,
    *,
    limit: int = _DEFAULT_RECALL_LIMIT,
    char_budget: int = _DEFAULT_RECALL_CHAR_BUDGET,
) -> list[dict[str, Any]]:
    clean_query = str(query or "").strip()
    if not clean_query or limit <= 0 or char_budget <= 0:
        return []
    query_signals = _signals(clean_query)
    scored = []
    for topic in load_topics():
        score = _score(clean_query, query_signals, topic)
        if score >= 6 and str(topic.get("summary") or "").strip():
            scored.append((score, topic))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("key") or "")))

    selected: list[dict[str, Any]] = []
    used = 0
    for score, topic in scored:
        summary = str(topic.get("summary") or "").strip()
        title = str(topic.get("title") or topic.get("key") or "").strip()
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
                **topic,
                "summary": summary,
                "score": score,
                "version": _topic_version(topic),
            }
        )
        used += cost
        if len(selected) >= limit:
            break
    return selected


def format_topic_recall(topics: Iterable[dict[str, Any]]) -> str:
    rows = list(topics)
    if not rows:
        return ""
    parts = [
        "Relevant topic summaries (current state; use session_search only when exact historical detail is needed):"
    ]
    for topic in rows:
        parts.append(f"### {topic.get('title') or topic.get('key')}\n{topic.get('summary') or ''}")
    return "\n\n".join(parts)


def review_topic_context(messages: Iterable[dict[str, Any]], *, limit: int = 3, char_budget: int = 2400) -> str:
    text_parts: list[str] = []
    for message in list(messages)[-18:]:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            text_parts.append(content[-1200:])
    selected = recall_topic_summaries("\n".join(text_parts), limit=limit, char_budget=char_budget)
    if not selected:
        return ""
    lines = [
        "Existing relevant topic summaries. If the conversation changes one, UPDATE the same topic key; "
        "preserve still-current facts and remove superseded ones instead of appending a second topic."
    ]
    for topic in selected:
        keywords = ", ".join(str(x) for x in topic.get("keywords") or [])
        lines.append(
            f"- topic={topic.get('key')} | title={topic.get('title')} | keywords={keywords}\n"
            f"  {topic.get('summary')}"
        )
    return "\n".join(lines)
