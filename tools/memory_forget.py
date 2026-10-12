"""Cross-store forget and replace for the memory tool.

A forget sweeps every store that holds the text: first the requested store's exact, unique match, then
MEMORY.md, USER.md and topic summaries (title, keywords and summary, case-insensitive). The result names each
store it changed in ``removed_from``. ``removed`` says what kind of item went (a count per memory store, or a
topic key and kind). Neither carries the forgotten text: a tool result is stored in the transcript, and
session_search could then find it again.

A replace keeps the requested store's exact match. When that store has none, a single hit in any store is
replaced in place, and several hits are refused because a replace must not guess.

A miss returns "not found" with short candidates from every store, so the model can retry with an exact phrase.
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
    """Short view of a current entry for candidate lists. Never used for forgotten text."""
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
    """Stores a forget may touch, in search order. Topic summaries are always included; a disabled built-in
    store is skipped because its writes are refused."""
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
                        "keywords": list(row.get("keywords") or []),
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
    """A short list for a retry: the entries sharing the most words with *needle*, or, when none share any,
    the first few entries of every store."""
    wanted = _tokens(needle)
    rows: List[Tuple[int, int, Dict[str, Any]]] = []
    for label in _enabled_labels(store):
        if label == "topic":
            for row in load_topics():
                shared = len(wanted & _tokens(topic_search_text(row)))
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
    """Whether *target* has a case-sensitive substring hit. An unreadable file counts as a hit so the store
    reports its own read failure."""
    entries = store.disk_entries(target)
    return entries is None or any(needle in entry for entry in entries)


def _topic_removed(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"store": "topic", "topic": item["key"], "what": item["what"]} for item in items]


def _sweep(store, needle: str, *, skip: Tuple[str, ...] = ()) -> Tuple[List[str], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Remove *needle* from every enabled store that holds it, except the stores in *skip*. Returns (stores changed,
    removed items, failure)."""
    hits, failure = _hits(store, needle)
    if failure is not None:
        return [], [], failure
    changed: List[str] = []
    removed: List[Dict[str, Any]] = []
    for label in _enabled_labels(store):
        if label in skip or not any(hit["store"] == label for hit in hits):
            continue
        if label == "topic":
            result = forget_topic_text(needle)
            if not result.get("success"):
                return changed, removed, result
            removed += _topic_removed(result["removed"])
        else:
            result = store.forget_matching(label, needle)
            if not result.get("success"):
                return changed, removed, result
            if result["removed_count"]:
                removed.append({"store": label, "count": result["removed_count"]})
        changed.append(label)
    return changed, removed, None


def _failed(failure: Dict[str, Any], stores: List[str], removed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A failure after some stores already changed: say which ones did."""
    if not stores:
        return failure
    return {**failure, "removed_from": stores, "removed": removed, "partial": True}


def _labels(stores: List[str]) -> str:
    return ", ".join(_LABELS[label] for label in stores)


def remove_entry(store, target: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Memory-tool remove. The requested store's exact, unique match is removed first, exactly as before, and
    then every other store that holds the text loses it too. With no exact match in the requested store, the
    sweep alone runs; a miss everywhere is "not found"."""
    needle = (old_text or "").strip()
    if not needle:
        return store.remove(target, old_text or "")
    stores: List[str] = []
    removed: List[Dict[str, Any]] = []
    if _exact_hit(store, target, needle):
        base = store.remove(target, needle)
        if not base.get("success"):
            return base
        stores.append(target)
        removed.append({"store": target, "count": 1})
    else:
        hits, failure = _hits(store, needle)
        if failure is not None:
            return failure
        if not hits:
            return _not_found(store, needle)
    swept, swept_removed, failure = _sweep(store, needle)
    if failure is not None:
        return _failed(failure, list(dict.fromkeys(stores + swept)), removed + swept_removed)
    all_stores = list(dict.fromkeys(stores + swept))
    response: Dict[str, Any] = {"success": True, "done": True, "target": target}
    if store is not None and target in ("memory", "user"):
        response["usage"] = store._usage(target)
        response["entry_count"] = len(store._entries_for(target))
    only_exact = all_stores == [target] and not swept_removed
    response["message"] = "Entry removed." if only_exact else f"Removed the text from {_labels(all_stores)}."
    response["removed_from"] = all_stores
    response["removed"] = removed + swept_removed
    response["note"] = _NOTE
    return response


def forget_owner_entry(store, target: str, text: str, *, index: Optional[int] = None) -> Dict[str, Any]:
    """Owner-initiated forget of one built-in entry: the memory page, the dashboard, or a journey card.

    The selected entry goes through the store's exact-match, drift and reset-generation checks (``index`` verifies
    its position). The same text is then removed from the other stores, as a memory-tool forget does. The selected
    store keeps its other entries, because the owner picked exactly one."""
    base = store.remove_index(target, index, text) if index is not None else store.remove_exact(target, text)
    if not base.get("success"):
        return base
    swept, removed, failure = _sweep(store, (text or "").strip(), skip=(target,))
    if failure is not None:
        return _failed(failure, [target] + swept, [{"store": target, "count": 1}] + removed)
    return {**base, "removed_from": [target] + swept, "removed": [{"store": target, "count": 1}] + removed,
            "message": f"Removed the text from {_labels([target] + swept)}."}


def remove_topic(store, topic: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Topic-target remove. A named topic with no phrase is removed whole (the existing contract). A phrase
    forgets that text everywhere, as a memory remove does; a phrase that is not in the named topic is searched
    for in every store."""
    phrase = (old_text or "").strip()
    named = get_topic_summary(topic) if topic else None
    if named is not None and not phrase:
        return remove_topic_summary(topic)
    needle = phrase or (topic or "").strip()
    if not needle:
        return _error("old_text cannot be empty.")
    hits, failure = _hits(store, needle)
    if failure is not None:
        return failure
    if not hits:
        return _not_found(store, needle)
    swept, removed, failure = _sweep(store, needle)
    if failure is not None:
        return _failed(failure, swept, removed)
    return {"success": True, "done": True, "target": "topic", "removed_from": swept, "removed": removed,
            "message": f"Removed the text from {_labels(swept)}.", "note": _NOTE}


def replace_entry(store, target: str, old_text: Optional[str], new_content: Optional[str], *,
                  owner_initiated: bool = False) -> Dict[str, Any]:
    """Memory-tool replace. An exact hit in *target* keeps the store's own response; otherwise a single hit in
    any store is replaced in place."""
    needle = (old_text or "").strip()
    if not needle or _exact_hit(store, target, needle):
        return store.replace(target, old_text or "", new_content or "", owner_initiated=owner_initiated)
    return _replace_everywhere(store, target, needle, new_content, owner_initiated=owner_initiated)


def _replace_everywhere(store, target: str, needle: str, new_content: Optional[str], *,
                        owner_initiated: bool) -> Dict[str, Any]:
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
        result = replace_topic_sentence(hit["key"], needle, content, owner_initiated=owner_initiated)
        return {**result, "done": True, "target": target, "replaced_in": "topic", "note": _NOTE} if result.get("success") else result
    result = store.replace_matching(hit["store"], needle, content, owner_initiated=owner_initiated)
    return {**result, "target": target, "replaced_in": hit["store"]} if result.get("success") else result


def batch_cross_store_hint(store, target: str, operations: List[Any]) -> Optional[Dict[str, Any]]:
    """A batch applies inside one store, all or nothing. A remove or replace whose phrase is not in *target* but
    is elsewhere is refused before anything is written, naming where it lives, instead of a bare 'no entry
    matched'."""
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
                f"{_labels(elsewhere)}. Nothing was applied. Send that {op['action']} as its own call without "
                "'operations' so every store is searched.")
    return None
