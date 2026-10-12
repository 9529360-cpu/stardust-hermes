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
from tools.memory_tombstones import (
    clear_forgotten_for, fold_text, forgotten_store_error, record_forgotten, recall_index, write_refusal,
)

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


def load_topics(*, strict: bool = False) -> list[dict[str, Any]]:
    return list(_read_payload(strict=strict)["topics"])


def topic_search_text(topic: dict[str, Any]) -> str:
    """Everything a topic is matched on by forget and recall: title, keywords, summary. Fields are joined
    with NUL, which folding keeps, so a phrase cannot match across two fields."""
    return "\x00".join([str(topic.get("title") or ""), *(str(k) for k in topic.get("keywords") or []),
                        str(topic.get("summary") or "")])


def _record_topic_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Record each removed topic as a whole-topic forget: its title and summary, under the hash of its key. Returns
    an error result, or None. Call it inside the topics lock, before the write: a removal that cannot be recorded
    does not happen."""
    try:
        for row in rows:
            texts = [str(text) for text in (row.get("title"), row.get("summary")) if text]
            key = _topic_key(row.get("key") or row.get("title"))
            if texts:
                record_forgotten(texts, source="topic", scope="topic", topic_key=key)
    except (OSError, RuntimeError) as exc:
        return {"success": False, "error": f"Nothing was removed: the forgotten record could not be written ({exc})."}
    return None


def _refused(reason: str) -> dict[str, Any]:
    """Result for a topic write that was not stored. The reason is logged and returned."""
    logger.info("Skipped a topic write: %s", reason)
    return {"success": False, "skipped": True, "reason": reason}


def _cleared(result: dict[str, Any], text: str, key: str) -> dict[str, Any]:
    """After an owner's own topic write: drop the records the text contains and the topic's whole-topic record."""
    try:
        cleared = clear_forgotten_for(text, topic_key=key)
    except (OSError, RuntimeError) as exc:
        return {**result, "success": False, "saved": True,
                "error": f"The topic was saved, but its forgotten record could not be cleared ({exc})."}
    if cleared:
        result["forgotten_cleared"] = cleared
    return result


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


def topic_candidate(topic: str, summary: str, *, title: str | None = None, keywords: Any = None) -> tuple[str, str]:
    """The topic key and the text an upsert would store (title, keywords and summary joined by NUL). A forgotten-text
    check uses exactly this text, so a refusal and the stored topic cannot disagree."""
    clean_title = str(title or topic or "").strip()[:_MAX_TITLE_CHARS]
    return _topic_key(topic or title), "\x00".join([clean_title, *_keywords(keywords), str(summary or "").strip()])


def upsert_topic_summary(
    topic: str,
    summary: str,
    *,
    title: str | None = None,
    keywords: Any = None,
    owner_initiated: bool = False,
) -> dict[str, Any]:
    key, candidate = topic_candidate(topic, summary, title=title, keywords=keywords)
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
    if reason := write_refusal(candidate, owner_initiated=owner_initiated, topic_key=key):
        return _refused(reason)

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
    result = {
        "success": True,
        "target": "topic",
        "topic": key,
        "title": row["title"],
        "message": f"Topic summary {change}",
        "version": _topic_version(row),
    }
    return _cleared(result, candidate, key) if owner_initiated else result


def reset_topic_summaries() -> bool:
    """Delete the local topic-summary store. Returns whether the file existed.

    Every erased topic is recorded as forgotten first, inside the lock. An unreadable file is refused rather than
    erased unrecorded, and a failed record raises before anything is erased."""
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if forget_error := forgotten_store_error():
        raise RuntimeError(forget_error)
    with MemoryStore._file_lock(path):
        existed = path.exists()
        erased = _read_payload(strict=True)["topics"] if existed else []
        if erased and (error := _record_topic_rows(erased)):
            raise RuntimeError(error["error"])
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
    if forget_error := forgotten_store_error():
        return {"success": False, "error": forget_error}
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        topics = list(payload["topics"])
        kept = [row for row in topics if _topic_key(row.get("key") or row.get("title")) != key]
        if len(kept) == len(topics):
            return {"success": False, "error": f"Topic '{topic}' was not found"}
        removed = [row for row in topics if _topic_key(row.get("key") or row.get("title")) == key]
        if error := _record_topic_rows(removed):
            return error
        _write_payload({"version": 1, "topics": kept})
    return {"success": True, "target": "topic", "topic": key, "message": "Topic summary removed"}


_SENTENCE_BREAK = re.compile(r"(?<=[。.!?！？；;\n])")


def _sentences(summary: str) -> list[str]:
    """Summary split into sentences (each keeps its terminator), the unit a forget removes."""
    return [piece for piece in _SENTENCE_BREAK.split(summary) if piece.strip()]


def topic_forget_units(row: dict[str, Any], needle: str) -> list[tuple[str, str]]:
    """What a forget of the folded *needle* removes from one topic row: ``("topic", title)`` when the title
    matches (the whole topic goes), otherwise ``("sentence", text)`` for each matching summary sentence and
    ``("keyword", text)`` for each matching keyword. The scan and the removal both use this, so they agree."""
    title = str(row.get("title") or "")
    if needle in fold_text(title):
        return [("topic", title)]
    units = [("sentence", s.strip()) for s in _sentences(str(row.get("summary") or "")) if needle in fold_text(s)]
    units += [("keyword", str(k)) for k in row.get("keywords") or [] if needle in fold_text(k)]
    return units


def forget_topic_text(phrase: str, *, topic_key: str | None = None) -> dict[str, Any]:
    """Forget *phrase* across topic summaries (or only topic *topic_key*) in one locked write.

    A title match removes the whole topic: the phrase names it. Otherwise only the summary sentences and keywords
    that contain the phrase go; the rest of the topic stays, and a topic left with an empty summary is removed.
    Every removed item is recorded as forgotten inside the lock, before the write. If that fails, nothing is removed.
    """
    needle = fold_text(phrase)
    if not needle:
        return {"success": False, "error": "old_text cannot be empty."}
    if forget_error := forgotten_store_error():
        return {"success": False, "error": forget_error}
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    removed: list[dict[str, str]] = []
    groups: dict[tuple[str, str], list[str]] = {}
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        kept: list[dict[str, Any]] = []
        for row in payload["topics"]:
            key = _topic_key(row.get("key") or row.get("title"))
            if topic_key is not None and key != topic_key:
                kept.append(row)
                continue
            units = topic_forget_units(row, needle)
            if not units:
                kept.append(row)
                continue
            if units[0][0] == "topic":
                removed.append({"key": key, "what": "topic"})
                groups.setdefault((key, "topic"), []).extend(
                    [str(row.get("title") or ""), str(row.get("summary") or "")])
                continue
            sentences = _sentences(str(row.get("summary") or ""))
            gone = [s for s in sentences if needle in fold_text(s)]
            keywords = [str(k) for k in row.get("keywords") or []]
            gone_keywords = [k for k in keywords if needle in fold_text(k)]
            removed += [{"key": key, "what": "summary sentence"} for _ in gone]
            removed += [{"key": key, "what": "keyword"} for _ in gone_keywords]
            if gone:
                groups.setdefault((key, "sentence"), []).extend(s.strip() for s in gone)
            if gone_keywords:
                groups.setdefault((key, "keyword"), []).extend(gone_keywords)
            remaining = "".join(s for s in sentences if s not in gone).strip()
            if remaining or not gone:
                # A topic is dropped only when its last summary sentence was forgotten.
                kept.append({**row, "summary": remaining, "keywords": [k for k in keywords if k not in gone_keywords],
                             "updated_at": _now()})
        if not removed:
            return {"success": False, "error": f"No topic summary matched '{phrase}'."}
        if len(removed) > 1:
            # Re-checked under the lock, before anything is recorded or written: the scan that chose this forget saw one
            # match, but another write may have added a second since then.
            return {"success": False, "error": f"'{phrase}' matches more than one topic item. Nothing was changed.",
                    "matches": removed}
        for (key, scope), texts in groups.items():
            try:
                record_forgotten(texts, source="topic", scope=scope, topic_key=key)
            except (OSError, RuntimeError) as exc:
                return {"success": False,
                        "error": f"Nothing was removed: the forgotten record could not be written ({exc})."}
        _write_payload({"version": 1, "topics": kept})
    return {
        "success": True,
        "target": "topic",
        "removed": removed,
        "message": f"Removed {len(removed)} matching topic text item{'' if len(removed) == 1 else 's'}.",
    }


def replace_topic_sentence(topic: str, phrase: str, content: str, *, owner_initiated: bool = False) -> dict[str, Any]:
    """Rewrite the single summary sentence of *topic* that contains *phrase*. Refused when no sentence or
    several sentences match: a replacement must not guess, and must not discard the rest of the summary."""
    key = _topic_key(topic)
    needle = fold_text(phrase)
    clean = str(content or "").strip()
    if not key or not needle or not clean:
        return {"success": False, "error": "topic, phrase and content are required"}
    if threat := _scan_memory_content(clean):
        return {"success": False, "error": threat}
    if reason := write_refusal(clean, owner_initiated=owner_initiated, topic_key=key):
        return _refused(reason)
    if forget_error := forgotten_store_error():
        return {"success": False, "error": forget_error}
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        topics = list(payload["topics"])
        idx = next((i for i, row in enumerate(topics) if _topic_key(row.get("key") or row.get("title")) == key), None)
        if idx is None:
            return {"success": False, "error": f"Topic '{topic}' was not found"}
        row = topics[idx]
        sentences = _sentences(str(row.get("summary") or ""))
        hits = [i for i, s in enumerate(sentences) if needle in fold_text(s)]
        if not hits:
            return {"success": False, "error": f"No sentence in topic '{topic}' contains '{phrase}'."}
        if len(hits) > 1:
            return {"success": False, "error": f"{len(hits)} sentences in topic '{topic}' contain '{phrase}'. Be more specific."}
        old = sentences[hits[0]]
        sentences[hits[0]] = clean + old[len(old.rstrip()):]
        summary = "".join(sentences).strip()
        if len(summary) > _MAX_SUMMARY_CHARS:
            return {"success": False, "error": f"Topic summary would be too long ({len(summary)}/{_MAX_SUMMARY_CHARS} chars)."}
        topics[idx] = {**row, "summary": summary, "updated_at": _now()}
        _write_payload({"version": 1, "topics": topics})
    # A replacement supersedes the old sentence; it is not forgotten, so nothing is recorded.
    result = {"success": True, "target": "topic", "topic": key, "title": str(row.get("title") or ""),
              "message": "Topic summary sentence replaced", "version": _topic_version(topics[idx])}
    return _cleared(result, clean, key) if owner_initiated else result


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
    forgotten = recall_index()
    if forgotten is None:
        return []  # forgotten.json cannot be read: topic recall is suspended rather than unfiltered
    query_signals = _signals(clean_query)
    scored = []
    for topic in load_topics():
        score = _score(clean_query, query_signals, topic)
        if score < 6 or not str(topic.get("summary") or "").strip():
            continue
        if forgotten.blocks_topic(_topic_key(topic.get("key") or topic.get("title"))):
            continue
        if not forgotten.mentions(topic_search_text(topic)):
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
