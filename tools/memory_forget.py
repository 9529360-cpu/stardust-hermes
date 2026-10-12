"""Cross-store forget and replace for the memory tool and the owner's memory surfaces.

A forget removes the text from every store that holds it, on exact hits too. Every store is scanned before anything
is written. Nothing changes, and the candidates are listed, when:

- the phrase is shorter than ``MIN_FORGET_CHARS`` normalized characters, so it could match unrelated text;
- the phrase matches more than one entry (or topic sentence, keyword or title) in any store; or
- a store, or forgotten.json, cannot be read or written.

Otherwise each store's single match is removed. The removed text is recorded as forgotten first, inside that store's
lock, so a removal that cannot be recorded does not happen.

A replace keeps the requested store's exact match. Otherwise a single hit in any store is replaced in place, and several
hits are refused, because a replace must not guess.

Responses name the stores and the kind of item removed (entry, sentence, keyword or topic). They never carry the removed
text, because a tool result is kept in the transcript and session_search could find it again.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.memory_tombstones import fold_text, forgotten_store_error, sanitize_legacy
from tools.memory_tool_store import _read_failed_error, _scan_memory_content
from tools.topic_memory_store import (
    forget_topic_text, get_topic_summary, load_topics, remove_topic_summary, replace_topic_sentence,
    topic_forget_units, topic_search_text,
)

MIN_FORGET_CHARS = 8
_LABELS = {"memory": "MEMORY.md", "user": "USER.md", "topic": "topic summaries"}
_NOTE = "Write saved. This update is complete — do not repeat it."
_CANDIDATE_LIMIT = 8
_PREVIEW_CHARS = 80
_Scan = Dict[str, List[Dict[str, Any]]]


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": message, **extra}


def _preview(text: Any) -> str:
    """Short view of a current entry, for candidate lists and refusals only. Never used in a success response."""
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


def _labels(stores: List[str]) -> str:
    return ", ".join(_LABELS[label] for label in stores)


def _enabled_labels(store) -> List[str]:
    """Stores a forget may touch, in search order. Topic summaries are always included; a disabled built-in store is
    skipped because its writes are refused."""
    built_in = [name for name in ("memory", "user") if store is not None and store.target_enabled(name)]
    return built_in + ["topic"]


def _memories_dir(store) -> Optional[Path]:
    return store._path_for("memory").parent if store is not None else None


def _scan(store, needle: str) -> Tuple[_Scan, Optional[Dict[str, Any]]]:
    """Every current match of the folded *needle*, per enabled store, or an error when a store cannot be read."""
    per_store: _Scan = {}
    for label in _enabled_labels(store):
        if label == "topic":
            try:
                rows = load_topics(strict=True)
            except (OSError, RuntimeError) as exc:
                return {}, _error(f"Could not read topic summaries ({exc}). Nothing was changed.")
            per_store["topic"] = [
                {"store": "topic", "what": what, "topic": str(row.get("key") or ""), "preview": _preview(text)}
                for row in rows for what, text in topic_forget_units(row, needle)
            ]
            continue
        entries = store.disk_entries(label)
        if entries is None:
            return {}, _read_failed_error(store._path_for(label))
        per_store[label] = [{"store": label, "what": "entry", "preview": _preview(entry)}
                            for entry in entries if needle in fold_text(entry)]
    return per_store, None


def _candidates(store, needle: str) -> List[Dict[str, Any]]:
    """A short list for a retry: the entries sharing the most words with *needle*, or, when none share any, the first
    few entries of every store."""
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


def _refusal(store, response: Dict[str, Any]) -> Dict[str, Any]:
    """A refused forget counts toward the turn's failure budget, so the model cannot loop on it."""
    return store._consolidation_failure(response) if store is not None else response


def _not_found(store, needle: str) -> Dict[str, Any]:
    return _refusal(store, _error(
        f"No entry matched '{needle}' in MEMORY.md, USER.md or topic summaries. Retry with an exact phrase copied from "
        "one of the candidates below.", candidates=_candidates(store, needle)))


def _too_short(store, needle: str) -> Dict[str, Any]:
    return _refusal(store, _error(
        f"'{needle}' is shorter than {MIN_FORGET_CHARS} characters, so it could match unrelated text. Nothing was "
        "changed. Retry with a longer phrase copied from one entry.", candidates=_candidates(store, needle)))


def _ambiguous(needle: str, per_store: _Scan) -> Dict[str, Any]:
    stores = [label for label, units in per_store.items() if len(units) > 1]
    listed = [unit for units in per_store.values() for unit in units]
    return _error(
        f"'{needle}' matches more than one entry in {_labels(stores)}. Nothing was changed. Retry with a longer phrase "
        "that identifies one entry.", matches=listed)


def _already_forgotten(target: str) -> Dict[str, Any]:
    return {"success": True, "done": True, "target": target, "already_forgotten": True, "removed_from": [],
            "removed": [], "message": "Nothing live matched: the text was already forgotten. Its legacy record was "
            "rewritten as a hash.", "note": _NOTE}


def _exact_hit(store, target: str, raw: str) -> bool:
    """Whether *target* has a case-sensitive substring hit. An unreadable file counts as a hit so the store reports its
    own read failure."""
    entries = store.disk_entries(target)
    return entries is None or any(raw in entry for entry in entries)


def _apply_unique(store, needle: str, per_store: _Scan) -> Tuple[List[str], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Remove the single match in each store that holds one. The caller has checked that no store holds more."""
    changed: List[str] = []
    removed: List[Dict[str, Any]] = []
    for label in _enabled_labels(store):
        if not per_store.get(label):
            continue
        if label == "topic":
            result = forget_topic_text(needle)
            if not result.get("success"):
                return changed, removed, result
            removed += [{"store": "topic", "topic": item["key"], "what": item["what"]} for item in result["removed"]]
        else:
            result = store.forget_matching(label, needle)
            if not result.get("success"):
                return changed, removed, result
            removed.append({"store": label, "what": "entry"})
        changed.append(label)
    return changed, removed, None


def _failed(failure: Dict[str, Any], changed: List[str], removed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A failure after some stores already changed: say which ones did."""
    if not changed:
        return failure
    return {**failure, "removed_from": changed, "removed": removed, "partial": True}


def _removed_response(store, target: str, changed: List[str], removed: List[Dict[str, Any]]) -> Dict[str, Any]:
    response: Dict[str, Any] = {"success": True, "done": True, "target": target}
    if store is not None and target in ("memory", "user"):
        response["usage"] = store._usage(target)
        response["entry_count"] = len(store._entries_for(target))
    only_target = changed == [target] and len(removed) == 1
    response["message"] = "Entry removed." if only_target else f"Removed the text from {_labels(changed)}."
    response["removed_from"] = changed
    response["removed"] = removed
    response["note"] = _NOTE
    return response


def _forget(store, target: str, needle: str) -> Dict[str, Any]:
    """The forget itself: scan every store, refuse unless each holds at most one match, then remove them all."""
    memories_dir = _memories_dir(store)
    if err := forgotten_store_error(memories_dir):
        return _error(err)
    try:
        _rewritten, legacy_hit = sanitize_legacy(memories_dir, needle=needle)
    except (OSError, RuntimeError) as exc:
        return _error(f"Nothing was removed: forgotten.json could not be read or rewritten ({exc}).")
    per_store, failure = _scan(store, needle)
    if failure is not None:
        return failure
    if not any(per_store.values()):
        return _already_forgotten(target) if legacy_hit else _not_found(store, needle)
    if any(len(units) > 1 for units in per_store.values()):
        return _ambiguous(needle, per_store)
    changed, removed, failure = _apply_unique(store, needle, per_store)
    if failure is not None:
        return _failed(failure, changed, removed)
    return _removed_response(store, target, changed, removed)


def remove_entry(store, target: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Memory-tool remove: the phrase is removed from every store that holds it, once and only if unique there."""
    needle = fold_text(old_text)
    if not needle:
        return store.remove(target, old_text or "")
    if len(needle) < MIN_FORGET_CHARS:
        return _too_short(store, needle)
    return _forget(store, target, needle)


def remove_topic(store, topic: str, old_text: Optional[str]) -> Dict[str, Any]:
    """Topic-target remove. A named topic with no phrase is removed whole (the existing contract). A phrase forgets
    that text everywhere, as a memory remove does."""
    phrase = (old_text or "").strip()
    if topic and get_topic_summary(topic) is not None and not phrase:
        return remove_topic_summary(topic)
    needle = fold_text(phrase or topic)
    if not needle:
        return _error("old_text cannot be empty.")
    if len(needle) < MIN_FORGET_CHARS:
        return _too_short(store, needle)
    response = _forget(store, "topic", needle)
    if response.get("success") and "usage" in response:
        response.pop("usage")
        response.pop("entry_count")
    return response


def forget_owner_entry(store, target: str, text: str, *, index: Optional[int] = None) -> Dict[str, Any]:
    """Owner-initiated forget of one built-in entry: the memory page, the dashboard, or a journey card.

    The selected entry goes through the store's exact-match, drift and reset-generation checks (``index`` verifies its
    position). Then the same text is removed from the other stores, as a memory-tool forget does. The sweep needs the
    text to be at least MIN_FORGET_CHARS long and unique in every other store; if it is not, nothing changes. A shorter
    text removes only the selected entry. The selected store keeps its other entries, because the owner picked one."""
    needle = fold_text(text)
    sweep = len(needle) >= MIN_FORGET_CHARS
    others: _Scan = {}
    if sweep:
        if err := forgotten_store_error(_memories_dir(store)):
            return _error(err)
        per_store, failure = _scan(store, needle)
        if failure is not None:
            return failure
        others = {label: units for label, units in per_store.items() if label != target}
        if any(len(units) > 1 for units in others.values()):
            return _ambiguous(needle, others)
    base = store.remove_index(target, index, text) if index is not None else store.remove_exact(target, text)
    if not base.get("success"):
        return base
    changed, removed, failure = _apply_unique(store, needle, others) if sweep else ([], [], None)
    selected = [{"store": target, "what": "entry"}]
    if failure is not None:
        return _failed(failure, [target] + changed, selected + removed)
    message = f"Removed the text from {_labels([target] + changed)}." if changed else "Entry removed."
    if not sweep:
        message += f" Not swept: the text is shorter than {MIN_FORGET_CHARS} characters."
    return {**base, "removed_from": [target] + changed, "removed": selected + removed, "message": message,
            "swept": sweep}


def replace_entry(store, target: str, old_text: Optional[str], new_content: Optional[str], *,
                  owner_initiated: bool = False) -> Dict[str, Any]:
    """Memory-tool replace. An exact, case-sensitive hit in *target* keeps the store's own response. Otherwise a single
    hit in any store is replaced in place. The phrase must be MIN_FORGET_CHARS long, as for a remove."""
    raw = (old_text or "").strip()
    needle = fold_text(raw)
    if not needle:
        return store.replace(target, old_text or "", new_content or "", owner_initiated=owner_initiated)
    if len(needle) < MIN_FORGET_CHARS:
        return _too_short(store, needle)
    if _exact_hit(store, target, raw):
        return store.replace(target, raw, new_content or "", owner_initiated=owner_initiated)
    return _replace_everywhere(store, target, needle, new_content, owner_initiated=owner_initiated)


def _replace_everywhere(store, target: str, needle: str, new_content: Optional[str], *,
                        owner_initiated: bool) -> Dict[str, Any]:
    content = (new_content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return _error("new_content cannot be empty. Use 'remove' to delete entries.")
    if scan_error := _scan_memory_content(content):
        return _error(scan_error)
    per_store, failure = _scan(store, needle)
    if failure is not None:
        return failure
    hits = [unit for units in per_store.values() for unit in units]
    if not hits:
        return _not_found(store, needle)
    if len(hits) > 1:
        return _error(f"Multiple entries matched '{needle}' across stores. Nothing was changed. Be more specific.",
                      matches=hits)
    hit = hits[0]
    if hit["store"] == "topic":
        if hit["what"] != "sentence":
            return _error(f"'{needle}' is in the title or keywords of topic '{hit['preview']}', not in a summary "
                          "sentence. Rewrite that topic with target='topic' and topic=<its key> instead.")
        result = replace_topic_sentence(hit["topic"], needle, content, owner_initiated=owner_initiated)
        return {**result, "done": True, "target": target, "replaced_in": "topic", "note": _NOTE} \
            if result.get("success") else result
    result = store.replace_matching(hit["store"], needle, content, owner_initiated=owner_initiated)
    return {**result, "target": target, "replaced_in": hit["store"]} if result.get("success") else result


def batch_cross_store_hint(store, target: str, operations: List[Any]) -> Optional[Dict[str, Any]]:
    """A batch applies inside one store, all or nothing. Before anything is written, a remove is refused when its phrase
    is too short, when it matches more than one entry in the target, or when it is also held by another store (naming
    that store). A replace whose phrase is not in the target but is elsewhere is refused the same way."""
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or op.get("action") not in ("remove", "replace"):
            continue
        raw = (op.get("old_text") or "").strip()
        needle = fold_text(raw)
        if not needle:
            continue
        action = op["action"]
        if action == "remove":
            if len(needle) < MIN_FORGET_CHARS:
                return _error(f"Operation {index + 1} (remove): '{raw}' is shorter than {MIN_FORGET_CHARS} characters. "
                              "Nothing was applied. Send a longer phrase as its own call.")
            per_store, failure = _scan(store, needle)
            if failure is not None:
                return failure
            elsewhere = [label for label, units in per_store.items() if label != target and units]
            if elsewhere:
                return _error(f"Operation {index + 1} (remove): '{raw}' is also in {_labels(elsewhere)}. Nothing was "
                              "applied. Send that remove as its own call without 'operations' so every store that holds "
                              "it loses it.")
            if len(per_store.get(target, [])) > 1:
                return _error(f"Operation {index + 1} (remove): '{raw}' matches more than one entry in "
                              f"{_LABELS.get(target, target)}. Nothing was applied. Use a longer phrase.")
            continue
        if _exact_hit(store, target, raw):
            continue
        per_store, failure = _scan(store, needle)
        if failure is not None:
            return failure
        elsewhere = [label for label, units in per_store.items() if label != target and units]
        if elsewhere:
            return _error(f"Operation {index + 1} (replace): '{raw}' is not in {_LABELS.get(target, target)} but matches "
                          f"{_labels(elsewhere)}. Nothing was applied. Send that replace as its own call without "
                          "'operations' so every store is searched.")
    return None
