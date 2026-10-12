"""Cross-store forget and replace for the memory tool.

``remove`` and ``replace`` look in the requested store first, exactly as before. Only when that
store has no case-sensitive substring hit does the search widen to every enabled store:
MEMORY.md, USER.md and topic summaries (title, keywords and summary, case-insensitive).

- One hit is acted on and reported in ``removed_from`` / ``replaced_in``.
- Several hits are all removed for a forget (each is listed) but refused for a replace, which must
  not guess which entry was meant.
- No hit anywhere is "not found", with short candidates from every store so the model can retry
  with an exact phrase.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from tools.memory_tombstones import fold_text
from tools.memory_tool_store import _read_failed_error, _scan_memory_content
from tools.topic_memory_store import (
    forget_topic_text, get_topic_summary, load_topics, remove_topic_summary, replace_topic_sentence,
    topic_search_text,
)

_LABELS = {"memory": "MEMORY.md", "user": "USER.md", "topic": "topic summaries"}
_NOTE = "Write saved. This update is complete — do not repeat it."
_CANDIDATE_LIMIT = 8
_PREVIEW_CHARS = 80


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": message, **extra}


def _preview(text: Any) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[:_PREVIEW_CHARS].rstrip() + "…"


def _tokens(text: str) -> set:
    """Words for candidate ranking: latin words of two or more letters, and CJK characters and bigrams."""
    folded = fold_text(text)
    words = set(re.findall(r"[a-z0-9]{2,}", folded))
    for run in re.findall(r"[㐀-鿿]+", folded):
        if len(run) == 1:
            words.add(run)
        else:
            words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def _enabled_labels(store) -> List[str]:
    """Stores a forget may touch, in search order. Topic summaries are always included; a disabled
    built-in store is skipped because its writes are refused."""
    built_in = [name for name in ("memory", "user") if store is not None and store.target_enabled(name)]
    return built_in + ["topic"]


def _hits(store, needle: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Every current hit for *needle* (case-insensitive) across the enabled stores, or an error when a store
    cannot be read."""
    folded = fold_text(needle)
    hits: List[Dict[str, Any]] = []
    for label in _enabled_labels(store):
        if label == "topic":
            try:
                rows = load_topics(strict=True)
            except (OSError, RuntimeError) as exc:
                return [], _error(f"Could not read topic summaries ({exc}). Nothing was removed.")
            for row in rows:
                if folded in fold_text(topic_search_text(row)):
                    hits.append({
                        "store": "topic", "key": str(row.get("key") or ""), "title": str(row.get("title") or ""),
                        "in_summary": folded in fold_text(row.get("summary")),
                        "preview": _preview(row.get("title") or row.get("key")),
                    })
            continue
        entries = store.disk_entries(label)
        if entries is None:
            return [], _read_failed_error(store._path_for(label))
        hits.extend({"store": label, "index": i, "preview": _preview(entry)}
                    for i, entry in enumerate(entries) if folded in fold_text(entry))
    return hits, None


def _candidates(store, needle: str) -> List[Dict[str, Any]]:
    """A short list for a retry: the entries sharing the most words with *needle*, or, when none share
    any, the first few entries of every store."""
    wanted = _tokens(needle)
    rows: List[Tuple[int, int, Dict[str, Any]]] = []
    for label in _enabled_labels(store):
        if label == "topic":
            for row in load_topics():
                text = topic_search_text(row)
                shared = len(wanted & _tokens(text))
                preview = _preview(f"{row.get('title') or row.get('key')}: {row.get('summary') or ''}")
                rows.append((shared, len(rows), {"store": "topic", "topic": str(row.get("key") or ""), "preview": preview}))
            continue
        for index, entry in enumerate(store.disk_entries(label) or []):
            rows.append((len(wanted & _tokens(entry)), len(rows),
                         {"store": label, "index": index, "preview": _preview(entry)}))
    ranked = sorted(rows, key=lambda row: (-row[0], row[1]))
    shared_any = [row[2] for row in ranked if row[0] > 0]
    return (shared_any or [row[2] for row in ranked])[:_CANDIDATE_LIMIT]


def _not_found(store, needle: str) -> Dict[str, Any]:
    response = _error(
        f"No entry matched '{needle}' in MEMORY.md, USER.md or topic summaries. Retry with an exact phrase "
        "copied from one of the candidates below.",
        candidates=_candidates(store, needle),
    )
    return store._consolidation_failure(response) if store is not None else response


def _exact_hit(store, target: str, needle: str) -> bool:
    """Whether *target* has a case-sensitive substring hit. An unreadable file counts as a hit so the
    store reports its own read failure."""
    entries = store.disk_entries(target)
    return entries is None or any(needle in entry for entry in entries)


def _partial(failure: Dict[str, Any], removed: List[Dict[str, Any]], removed_from: List[str]) -> Dict[str, Any]:
    if not removed:
        return failure
    return {**failure, "removed_from": removed_from, "removed": removed, "partial": True}


def _remove_everywhere(store, target: str, needle: str) -> Dict[str, Any]:
    if not needle:
        return _error("old_text cannot be empty.")
    hits, failure = _hits(store, needle)
    if failure is not None:
        return failure
    if not hits:
        return _not_found(store, needle)
    removed: List[Dict[str, Any]] = []
    removed_from: List[str] = []
    for label in _enabled_labels(store):
        if not any(hit["store"] == label for hit in hits):
            continue
        if label == "topic":
            result = forget_topic_text(needle)
            if not result.get("success"):
                return _partial(result, removed, removed_from)
            removed += _topic_removed(result["removed"])
        else:
            result = store.forget_matching(label, needle)
            if not result.get("success"):
                return _partial(result, removed, removed_from)
            removed += [{"store": label, "preview": _preview(text)} for text in result["removed_texts"]]
        removed_from.append(label)
    count = len(removed)
    return {
        "success": True,
        "done": True,
        "target": target,
        "removed_from": removed_from,
        "removed": removed,
        "message": (f"Removed {count} matching entr{'y' if count == 1 else 'ies'} from "
                    f"{', '.join(_LABELS[label] for label in removed_from)}; the requested store had no exact match, "
                    "so every store was searched."),
        "note": _NOTE,
    }


def remove_entry(store, target: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Memory-tool remove for MEMORY.md / USER.md. An exact hit in *target* keeps the store's own response;
    otherwise every store is searched."""
    needle = (old_text or "").strip()
    if not needle or _exact_hit(store, target, needle):
        return store.remove(target, old_text or "")
    return _remove_everywhere(store, target, needle)


def remove_topic(store, topic: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Topic-target remove.

    A named topic with no phrase is removed whole (the existing contract). A phrase given with the topic
    forgets just that phrase inside the topic, so a one-fact forget cannot wipe the rest of the topic. When the
    phrase is not in that topic, every store is searched for it (old_text, else the topic name)."""
    phrase = (old_text or "").strip()
    named = get_topic_summary(topic) if topic else None
    if named is not None and not phrase:
        return remove_topic_summary(topic)
    if named is not None:
        result = forget_topic_text(phrase, topic_key=str(named.get("key") or ""))
        if result.get("success"):
            return {"success": True, "done": True, "target": "topic", "removed_from": ["topic"],
                    "removed": _topic_removed(result["removed"]), "message": result["message"], "note": _NOTE}
    return _remove_everywhere(store, "topic", phrase or (topic or "").strip())


def _topic_removed(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Response rows for topic text removed by forget_topic_text."""
    return [{"store": "topic", "topic": item["key"], "what": item["what"], "preview": _preview(item["text"])}
            for item in items]


def replace_entry(store, target: str, old_text: Optional[str], new_content: Optional[str]) -> Dict[str, Any]:
    """Memory-tool replace. An exact hit in *target* keeps the store's own response; otherwise a single hit
    in any store is replaced in place."""
    needle = (old_text or "").strip()
    if not needle or _exact_hit(store, target, needle):
        return store.replace(target, old_text or "", new_content or "")
    return _replace_everywhere(store, target, needle, new_content)


def _replace_everywhere(store, target: str, needle: str, new_content: Optional[str]) -> Dict[str, Any]:
    content = (new_content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return _error("new_content cannot be empty. Use 'remove' to delete entries.")
    if scan_error := _scan_memory_content(content):
        return _error(scan_error)
    hits, failure = _hits(store, needle)
    if failure is not None:
        return failure
    if not hits:
        return _not_found(store, needle)
    if len(hits) > 1:
        return _error(f"Multiple entries matched '{needle}' across stores. Be more specific.",
                      matches=[{"store": hit["store"], "preview": hit["preview"]} for hit in hits])
    hit = hits[0]
    if hit["store"] == "topic":
        if not hit["in_summary"]:
            return _error(f"'{needle}' is in the title or keywords of topic '{hit['title'] or hit['key']}', not in a "
                          "summary sentence. Rewrite that topic with target='topic' and topic=<its key> instead.")
        # Only the one summary sentence that contains the phrase is rewritten; the rest of the topic stays.
        result = replace_topic_sentence(hit["key"], needle, content)
        return {**result, "done": True, "target": target, "replaced_in": "topic", "note": _NOTE} if result.get("success") else result
    result = store.replace_matching(hit["store"], needle, content)
    return {**result, "target": target, "replaced_in": hit["store"]} if result.get("success") else result


def batch_cross_store_hint(store, target: str, operations: List[Any]) -> Optional[Dict[str, Any]]:
    """A batch applies inside one store, all or nothing. A remove or replace whose phrase is not in *target*
    but is elsewhere is refused before anything is written, naming where it lives, instead of a bare
    'no entry matched'."""
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or op.get("action") not in ("remove", "replace"):
            continue
        needle = (op.get("old_text") or "").strip()
        if not needle or _exact_hit(store, target, needle):
            continue
        hits, _failure = _hits(store, needle)
        elsewhere = [label for label in _enabled_labels(store)
                     if label != target and any(hit["store"] == label for hit in hits)]
        if elsewhere:
            return _error(
                f"Operation {index + 1} ({op['action']}): '{needle}' is not in {_LABELS.get(target, target)} but matches "
                f"{', '.join(_LABELS[label] for label in elsewhere)}. Nothing was applied. Send that {op['action']} "
                "as its own call without 'operations' so every store is searched.")
    return None
