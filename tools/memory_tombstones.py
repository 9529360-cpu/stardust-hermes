"""Forgotten-text records: what the owner removed, kept as hashes so recall and writes can be checked.

``memories/forgotten.json`` sits beside MEMORY.md and TOPICS.json. A record holds the SHA-256 of the normalized
forgotten text, that text's normalized length (needed to find it inside a longer passage), the time, the source
store, the scope (``entry``, ``sentence``, ``keyword`` or ``topic``) and, for topic text, the SHA-256 of the topic
key. Neither the text nor the topic key is stored. Plaintext rows from the earlier build are hashed when read, and
the file is written back without them.

Normalization (``fold_text``): Unicode NFKC, then casefold, then runs of whitespace collapsed to one space. Matching is
verbatim after that: a paraphrase is not matched.

- Recall (session_search and topic recall) drops a result whose normalized text contains a recorded phrase. When the
  file cannot be read, recall is suspended rather than unfiltered.
- Writes that do not come from the owner (background review, staged proposals, agent import, the agent's own adds)
  refuse text that contains a recorded phrase, and refuse a topic whose whole-topic record matches. An unreadable
  file refuses every memory write.
- An owner's own add clears the records it contains, so the owner can re-add what they forgot.
- Recording happens inside the removal's lock, before the write. If the record cannot be written, the removal does
  not happen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from utils import atomic_write_text

logger = logging.getLogger(__name__)

FORGOTTEN_FILENAME = "forgotten.json"
MIN_PHRASE_CHARS = 4  # recall and re-add checks; phrases shorter than this are recorded but never matched
SCOPES = ("entry", "sentence", "keyword", "topic")
REFUSAL_TEXT = (
    "The text repeats something the owner forgot. Automatic writes cannot put it back; "
    "the owner can add it directly from the memory page."
)
REFUSAL_TOPIC = (
    "This topic was forgotten as a whole. Automatic writes cannot recreate it; the owner can add it "
    "directly from the memory page."
)
REFUSAL_UNREADABLE = (
    "forgotten.json cannot be read ({problem}). Repair it before any memory write, so forgotten text cannot "
    "come back."
)
RECALL_SUSPENDED = (
    "Recall is suspended because forgotten.json cannot be read, so forgotten text could not be filtered. "
    "Repair forgotten.json and retry."
)
_FORMAT_VERSION = 2
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MENTION_CACHE_SIZE = 512


class ForgottenProposalRefused(RuntimeError):
    """A staged proposal would add text the owner forgot. Refused before anything is written to disk."""


def fold_text(value: Any) -> str:
    """Comparison form shared by every forget, recall and re-add match: NFKC, casefold, collapsed whitespace."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _sha(folded: str) -> str:
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def text_digest(text: Any) -> str:
    """SHA-256 of the normalized text: the value stored in a record and compared on recall."""
    return _sha(fold_text(text))


def topic_digest(key: Any) -> str:
    """SHA-256 of the normalized topic key. The key itself is never stored."""
    return _sha(fold_text(key))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def forgotten_path(memories_dir: Optional[Path] = None) -> Path:
    """The forgotten-records file beside *memories_dir* (default: the active profile's memories)."""
    if memories_dir is not None:
        return memories_dir / FORGOTTEN_FILENAME
    from tools import memory_tool  # resolved per call: profiles and tests redirect the memories dir

    return memory_tool.get_memory_dir() / FORGOTTEN_FILENAME


def _normalize_row(row: Any) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(usable record or None, is_legacy). A legacy row holds plaintext ``text`` or a topic slug."""
    if not isinstance(row, dict):
        return None, True
    if isinstance(row.get("sha256"), str) and _DIGEST.fullmatch(row["sha256"]) \
            and isinstance(row.get("length"), int) and row["length"] >= 0:
        record: Dict[str, Any] = {"sha256": row["sha256"], "length": row["length"]}
        legacy = False
    elif isinstance(row.get("text"), str):
        folded = fold_text(row["text"])
        if not folded:
            return None, True
        record = {"sha256": _sha(folded), "length": len(folded)}
        legacy = True
    else:
        return None, True
    record["forgotten_at"] = str(row.get("forgotten_at") or "")
    record["source"] = str(row.get("source") or "")
    scope = row.get("scope") if row.get("scope") in SCOPES else ("sentence" if row.get("topic") else "entry")
    record["scope"] = scope
    if isinstance(row.get("topic_sha256"), str) and _DIGEST.fullmatch(row["topic_sha256"]):
        record["topic_sha256"] = row["topic_sha256"]
    elif isinstance(row.get("topic"), str) and row["topic"]:
        record["topic_sha256"] = topic_digest(row["topic"])
        legacy = True
    return record, legacy


def _unusable(strict: bool, problem: str) -> Tuple[List[Dict[str, Any]], int]:
    if strict:
        raise RuntimeError(f"{FORGOTTEN_FILENAME} {problem}; refusing to overwrite it")
    logger.warning("Ignoring unreadable %s (%s); recall is suspended and writes are refused", FORGOTTEN_FILENAME, problem)
    return [], 0


def _load(path: Path, *, strict: bool) -> Tuple[List[Dict[str, Any]], int]:
    """(records, legacy row count). Strict callers raise on anything unusable, so a write never replaces bytes it
    could not parse. Tolerant readers skip unusable rows."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], 0
    except OSError:
        if strict:
            raise
        logger.warning("Could not read %s", FORGOTTEN_FILENAME, exc_info=True)
        return [], 0
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return _unusable(strict, "is not valid UTF-8")
    except ValueError:
        return _unusable(strict, "is not valid JSON")
    rows = data.get("forgotten") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return _unusable(strict, "has an invalid forgotten payload")
    records: List[Dict[str, Any]] = []
    legacy = 0
    for row in rows:
        record, is_legacy = _normalize_row(row)
        if record is None:
            if strict:
                raise RuntimeError(f"{FORGOTTEN_FILENAME} has records that cannot be read; refusing to overwrite it")
            logger.warning("Skipping an unreadable record in %s", FORGOTTEN_FILENAME)
            continue
        records.append(record)
        legacy += int(is_legacy)
    return records, legacy


def _write(path: Path, records: List[Dict[str, Any]]) -> None:
    """Caller holds the file lock."""
    atomic_write_text(
        path,
        json.dumps({"version": _FORMAT_VERSION, "forgotten": records}, ensure_ascii=False, indent=2) + "\n",
        tmp_prefix=".forgotten.",
        preserve_mode=True,
        create_mode=0o600,
        fsync_dir=True,
    )


def _lock(path: Path):
    from tools.memory_tool_store import MemoryStore  # lazy: memory_tool_store imports this module

    return MemoryStore._file_lock(path)


def forgotten_store_error(memories_dir: Optional[Path] = None) -> Optional[str]:
    """Why a removal cannot be recorded right now, or None. Checked before anything is removed."""
    try:
        _load(forgotten_path(memories_dir), strict=True)
    except (OSError, RuntimeError) as exc:
        return (f"Refusing to forget: {FORGOTTEN_FILENAME} cannot be read ({exc}). Repair or move that file so "
                "forgotten text can still be checked, then retry. Nothing was removed.")
    return None


def record_forgotten(texts: Iterable[str], *, source: str, scope: str, topic_key: Optional[str] = None,
                     memories_dir: Optional[Path] = None) -> None:
    """Record the hashes of *texts*. A text already recorded keeps its first record.

    Raises RuntimeError (or OSError) when the records cannot be written. Callers hold their own store lock and
    run this before writing the removal, so a failure leaves the removal undone.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown forgotten scope {scope!r}")
    folded_texts = list(dict.fromkeys(folded for folded in (fold_text(text) for text in texts) if folded))
    if not folded_texts:
        return
    path = forgotten_path(memories_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock(path):
        records, _legacy = _load(path, strict=True)
        known = {record["sha256"] for record in records}
        stamp = _now()
        added = 0
        for folded in folded_texts:
            digest = _sha(folded)
            if digest in known:
                continue
            known.add(digest)
            record: Dict[str, Any] = {"sha256": digest, "length": len(folded), "forgotten_at": stamp,
                                      "source": source, "scope": scope}
            if topic_key:
                record["topic_sha256"] = topic_digest(topic_key)
            records.append(record)
            added += 1
        if added:
            _write(path, records)


class ForgottenIndex:
    """Recorded digests indexed by length, for finding a recorded text inside a longer passage.

    Finding a record inside a passage needs a SHA-256 of every window of the record's length, so the cost is the
    passage length times the number of distinct record lengths. ``mentions`` caches its answer per passage and index.
    """

    _mention_cache: "OrderedDict[Tuple[str, str], bool]" = OrderedDict()
    _mention_lock = threading.Lock()

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._by_length: Dict[int, Set[str]] = {}
        self._topic_blocks: Set[str] = set()
        for record in records:
            if record["length"] >= MIN_PHRASE_CHARS:
                self._by_length.setdefault(record["length"], set()).add(record["sha256"])
            if record.get("scope") == "topic" and record.get("topic_sha256"):
                self._topic_blocks.add(record["topic_sha256"])
        pairs = sorted((digest, length) for length, digests in self._by_length.items() for digest in digests)
        self.fingerprint = hashlib.sha256(
            json.dumps([pairs, sorted(self._topic_blocks)]).encode("utf-8")).hexdigest()

    def __bool__(self) -> bool:
        return bool(self._by_length) or bool(self._topic_blocks)

    def blocks_topic(self, key: Any) -> bool:
        """Whether a whole-topic record matches this topic key (compared by hash)."""
        return topic_digest(key) in self._topic_blocks

    def contained(self, text: Any, *, first_only: bool = False) -> Set[str]:
        """Digests of the recorded texts that occur verbatim in *text* once folded."""
        folded = fold_text(text)
        size = len(folded)
        found: Set[str] = set()
        for length in sorted(self._by_length):
            if length > size:
                break
            wanted = self._by_length[length]
            for start in range(size - length + 1):
                # A recorded text starts and ends on a non-space character, so other windows cannot match.
                if folded[start] == " " or folded[start + length - 1] == " ":
                    continue
                digest = _sha(folded[start:start + length])
                if digest in wanted:
                    found.add(digest)
                    if first_only:
                        return found
        return found

    def mentions(self, text: Any) -> bool:
        if not self._by_length:
            return False
        key = (self.fingerprint, _sha(fold_text(text)))
        with ForgottenIndex._mention_lock:
            cached = ForgottenIndex._mention_cache.get(key)
        if cached is not None:
            return cached
        result = bool(self.contained(text, first_only=True))
        with ForgottenIndex._mention_lock:
            ForgottenIndex._mention_cache[key] = result
            while len(ForgottenIndex._mention_cache) > _MENTION_CACHE_SIZE:
                ForgottenIndex._mention_cache.popitem(last=False)
        return result


_INDEX_CACHE: Dict[str, Tuple[str, ForgottenIndex]] = {}


def forgotten_index(memories_dir: Optional[Path] = None, *, strict: bool = False) -> ForgottenIndex:
    """Index of the recorded texts. Strict callers get OSError or RuntimeError for an unreadable file."""
    path = forgotten_path(memories_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ForgottenIndex([])
    except OSError:
        if strict:
            raise
        logger.warning("Could not read %s", FORGOTTEN_FILENAME, exc_info=True)
        return ForgottenIndex([])
    content = hashlib.sha256(raw).hexdigest()
    cached = _INDEX_CACHE.get(str(path))
    if cached is not None and cached[0] == content:
        return cached[1]
    records, _legacy = _load(path, strict=strict)
    index = ForgottenIndex(records)
    _INDEX_CACHE[str(path)] = (content, index)
    return index


def recall_index(memories_dir: Optional[Path] = None) -> Optional[ForgottenIndex]:
    """The index for recall, or None when the file cannot be read: recall must then be suspended."""
    try:
        return forgotten_index(memories_dir, strict=True)
    except (OSError, RuntimeError):
        logger.warning("Recall suspended: %s cannot be read", FORGOTTEN_FILENAME, exc_info=True)
        return None


def write_refusal(text: Any, *, owner_initiated: bool, topic_key: Optional[str] = None,
                  memories_dir: Optional[Path] = None) -> Optional[str]:
    """The reason a memory write must not store *text* (for *topic_key*, a topic), or None when it may.

    An unreadable file refuses every write, owner's included. A non-owner write refuses forgotten text and a
    forgotten topic key. An owner's write may put forgotten text back; it clears the matching records afterwards.
    """
    try:
        index = forgotten_index(memories_dir, strict=True)
    except (OSError, RuntimeError) as exc:
        return REFUSAL_UNREADABLE.format(problem=exc)
    if owner_initiated or not index:
        return None
    if topic_key and index.blocks_topic(topic_key):
        return REFUSAL_TOPIC
    if index.mentions(text):
        return REFUSAL_TEXT
    return None


def proposal_refusal(payload: Dict[str, Any], memories_dir: Optional[Path] = None) -> Optional[str]:
    """Refusal for a staged memory proposal that would add forgotten text: a memory add or replace (single or inside a
    batch), or a topic upsert, checked as the title, keywords and summary it would store, under its topic key. Proposals
    are never owner-initiated, so the owner adds such text directly."""
    if payload.get("target") == "topic":
        if payload.get("action") not in ("upsert", "add", "replace"):
            return None
        from tools.topic_memory_store import topic_candidate  # lazy: topic_memory_store imports this module

        key, candidate = topic_candidate(payload.get("topic") or payload.get("title") or "", payload.get("content") or "",
                                         title=payload.get("title"), keywords=payload.get("keywords"))
        return write_refusal(candidate, owner_initiated=False, topic_key=key, memories_dir=memories_dir)
    texts: List[str] = []
    if payload.get("action") in ("add", "replace"):
        texts.append(payload.get("content") or payload.get("new_text") or "")
    for op in payload.get("operations") or []:
        if isinstance(op, dict) and op.get("action") in ("add", "replace"):
            texts.append(op.get("content") or op.get("new_text") or "")
    for text in texts:
        if reason := write_refusal(text, owner_initiated=False, memories_dir=memories_dir):
            return reason
    return None


def clear_forgotten_for(text: Any, *, topic_key: Optional[str] = None,
                        memories_dir: Optional[Path] = None) -> int:
    """After an owner's own write: drop the records whose text occurs in *text*, and the whole-topic record for
    *topic_key*. Raises when the file cannot be written. Returns the number of records dropped."""
    path = forgotten_path(memories_dir)
    if not path.exists():
        return 0
    with _lock(path):
        records, _legacy = _load(path, strict=True)
        hit = ForgottenIndex(records).contained(text)
        topic = topic_digest(topic_key) if topic_key else None
        kept = [record for record in records if record["sha256"] not in hit and not (
            topic and record.get("scope") == "topic" and record.get("topic_sha256") == topic)]
        if len(kept) == len(records):
            return 0
        _write(path, kept)
        return len(records) - len(kept)


def sanitize_legacy(memories_dir: Optional[Path] = None, *, needle: Optional[str] = None) -> Tuple[int, bool]:
    """Rewrite legacy plaintext rows as hashes, under the lock. Returns (rows rewritten, whether a legacy row's
    text contains *needle*). A forget calls this first, so even one that finds only a legacy row removes its
    plaintext from the file."""
    path = forgotten_path(memories_dir)
    if not path.exists():
        return 0, False
    folded_needle = fold_text(needle) if needle else ""
    with _lock(path):
        records, legacy = _load(path, strict=True)  # raises when the file cannot be parsed
        matched = False
        if folded_needle and legacy:
            for row in json.loads(path.read_bytes().decode("utf-8")).get("forgotten") or []:
                if isinstance(row, dict) and isinstance(row.get("text"), str) \
                        and folded_needle in fold_text(row["text"]):
                    matched = True
        if legacy:
            _write(path, records)
        return legacy, matched
