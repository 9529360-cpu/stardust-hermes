"""Forgotten-text records: what the owner removed, kept as hashes so recall and re-adds can be checked.

``memories/forgotten.json`` sits beside MEMORY.md and TOPICS.json. A record holds the SHA-256 of the
normalized forgotten text, that text's normalized length (needed to find it inside a longer passage),
the time, the source store and, for topic text, the topic key. The text itself is never stored.
Records written by the earlier build hold plaintext; they are hashed when read and written back without
it on the next write.

- Recall (session_search and topic recall) drops a result whose normalized text contains a recorded phrase.
- Automatic writers (background review, staged approvals, agent import) do not store text that contains a
  recorded phrase. They refuse it and log the reason.
- An explicit owner add of such text clears the records it contains, so the owner can re-add it.

Matching is verbatim after Unicode-compatibility, case and whitespace folding. A paraphrase is not matched,
and phrases shorter than ``MIN_PHRASE_CHARS`` are recorded but never used.
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
MIN_PHRASE_CHARS = 4
REFUSAL_REASON = (
    "The text repeats something the owner forgot. Automatic writes cannot put it back; "
    "the owner can add it directly from the memory page."
)
_FORMAT_VERSION = 2
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MENTION_CACHE_SIZE = 512


def fold_text(value: Any) -> str:
    """Comparison form shared by every forget, recall and re-add match."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _digest(folded: str) -> str:
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def forgotten_path(memories_dir: Optional[Path] = None) -> Path:
    """The forgotten-records file beside *memories_dir* (default: the active profile's memories)."""
    if memories_dir is not None:
        return memories_dir / FORGOTTEN_FILENAME
    from tools import memory_tool  # resolved per call: profiles and tests redirect the memories dir

    return memory_tool.get_memory_dir() / FORGOTTEN_FILENAME


def _record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    """A usable record from a stored row, or None. A legacy plaintext row is hashed here."""
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("sha256"), str) and _DIGEST.fullmatch(row["sha256"]) and isinstance(row.get("length"), int) \
            and row["length"] >= 0:
        record: Dict[str, Any] = {"sha256": row["sha256"], "length": row["length"]}
    elif isinstance(row.get("text"), str):
        folded = fold_text(row["text"])
        if not folded:
            return None
        record = {"sha256": _digest(folded), "length": len(folded)}
    else:
        return None
    record["forgotten_at"] = str(row.get("forgotten_at") or "")
    record["source"] = str(row.get("source") or "")
    if isinstance(row.get("topic"), str) and row["topic"]:
        record["topic"] = row["topic"]
    return record


def _unusable(strict: bool, problem: str) -> List[Dict[str, Any]]:
    """Strict callers refuse, so a write never replaces bytes it could not parse; tolerant readers warn and
    treat the file as empty, which only stops recall and re-add checks from filtering."""
    if strict:
        raise RuntimeError(f"{FORGOTTEN_FILENAME} {problem}; refusing to overwrite it")
    logger.warning("Ignoring unreadable %s (%s); recall and re-add checks are not filtering forgotten text",
                   FORGOTTEN_FILENAME, problem)
    return []


def _parse_records(raw: bytes, *, strict: bool) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return _unusable(strict, "is not valid UTF-8")
    except ValueError:
        return _unusable(strict, "is not valid JSON")
    rows = data.get("forgotten") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return _unusable(strict, "has an invalid forgotten payload")
    records = [_record_from_row(row) for row in rows]
    usable = [record for record in records if record is not None]
    if len(usable) != len(rows):
        if strict:
            raise RuntimeError(f"{FORGOTTEN_FILENAME} has records that cannot be read; refusing to overwrite it")
        logger.warning("Skipping %d unreadable record(s) in %s", len(rows) - len(usable), FORGOTTEN_FILENAME)
    return usable


def _read_records(path: Path, *, strict: bool) -> List[Dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        logger.warning("Could not read %s; recall and re-add checks are not filtering forgotten text",
                       FORGOTTEN_FILENAME, exc_info=True)
        return []
    return _parse_records(raw, strict=strict)


def _write_records(path: Path, records: List[Dict[str, Any]]) -> None:
    """Caller holds the file lock."""
    atomic_write_text(
        path,
        json.dumps({"version": _FORMAT_VERSION, "forgotten": records}, ensure_ascii=False, indent=2) + "\n",
        tmp_prefix=".forgotten.",
        preserve_mode=True,
        create_mode=0o600,
        fsync_dir=True,
    )


def forgotten_store_error(memories_dir: Optional[Path] = None) -> Optional[str]:
    """Why forgetting cannot be recorded right now, or None. Checked BEFORE an entry is removed, so nothing
    is removed without its record."""
    try:
        _read_records(forgotten_path(memories_dir), strict=True)
    except (OSError, RuntimeError) as exc:
        return (f"Refusing to forget: {FORGOTTEN_FILENAME} cannot be read ({exc}). Repair or move that file so "
                "forgotten text can still be checked, then retry. Nothing was removed.")
    return None


def record_forgotten(texts: Iterable[str], *, source: str, topic: Optional[str] = None,
                     memories_dir: Optional[Path] = None) -> Optional[str]:
    """Record the hashes of *texts* as forgotten. A text already recorded keeps its first record.

    Returns None on success, or a warning when the records could not be written. The removal they describe has
    already happened, so this reports instead of raising.
    """
    from tools.memory_tool_store import MemoryStore  # lazy: memory_tool_store imports this module

    folded_texts = list(dict.fromkeys(folded for folded in (fold_text(text) for text in texts) if folded))
    if not folded_texts:
        return None
    path = forgotten_path(memories_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with MemoryStore._file_lock(path):
            records = _read_records(path, strict=True)
            known = {record["sha256"] for record in records}
            stamp = _now()
            added = 0
            for folded in folded_texts:
                digest = _digest(folded)
                if digest in known:
                    continue
                known.add(digest)
                record: Dict[str, Any] = {"sha256": digest, "length": len(folded), "forgotten_at": stamp,
                                          "source": source}
                if topic:
                    record["topic"] = topic
                records.append(record)
                added += 1
            if added:
                _write_records(path, records)
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not record forgotten text in %s", path.name, exc_info=True)
        return (f"The entry was removed, but recording it in {FORGOTTEN_FILENAME} failed ({exc}); old chats may "
                "still surface it in recall until that is repaired.")
    return None


class ForgottenIndex:
    """Recorded digests indexed by length, for finding a recorded text inside a longer passage.

    Finding a record inside a passage needs a SHA-256 of every window of the record's length, so the cost is the
    passage length times the number of distinct record lengths. ``mentions`` caches its answer per passage and
    index, so a passage that is checked again (a topic on every turn) costs a lookup.
    """

    _mention_cache: "OrderedDict[Tuple[str, str], bool]" = OrderedDict()
    _mention_lock = threading.Lock()

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._by_length: Dict[int, Set[str]] = {}
        for record in records:
            if record["length"] >= MIN_PHRASE_CHARS:
                self._by_length.setdefault(record["length"], set()).add(record["sha256"])
        pairs = sorted((digest, length) for length, digests in self._by_length.items() for digest in digests)
        self.fingerprint = hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()

    def __bool__(self) -> bool:
        return bool(self._by_length)

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
                digest = _digest(folded[start:start + length])
                if digest in wanted:
                    found.add(digest)
                    if first_only:
                        return found
        return found

    def mentions(self, text: Any) -> bool:
        if not self._by_length:
            return False
        key = (self.fingerprint, _digest(fold_text(text)))
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


def forgotten_index(memories_dir: Optional[Path] = None) -> ForgottenIndex:
    """Index of the recorded texts for recall and re-add checks (tolerant read). Parsed once per distinct file
    content, so a rewritten file is always picked up."""
    path = forgotten_path(memories_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ForgottenIndex([])
    except OSError:
        logger.warning("Could not read %s; recall and re-add checks are not filtering forgotten text",
                       FORGOTTEN_FILENAME, exc_info=True)
        return ForgottenIndex([])
    content = hashlib.sha256(raw).hexdigest()
    cached = _INDEX_CACHE.get(str(path))
    if cached is not None and cached[0] == content:
        return cached[1]
    index = ForgottenIndex(_parse_records(raw, strict=False))
    _INDEX_CACHE[str(path)] = (content, index)
    return index


def automatic_write_refusal(text: Any, memories_dir: Optional[Path] = None) -> Optional[str]:
    """The reason an automatic writer must not store *text*, or None when it may."""
    return REFUSAL_REASON if forgotten_index(memories_dir).mentions(text) else None


def clear_forgotten_within(text: Any, memories_dir: Optional[Path] = None) -> Tuple[int, Optional[str]]:
    """Drop the records whose text occurs in *text*. An explicit owner add puts that text back on purpose.

    Returns (records cleared, warning or None).
    """
    from tools.memory_tool_store import MemoryStore  # lazy: memory_tool_store imports this module

    path = forgotten_path(memories_dir)
    if not fold_text(text) or not path.exists():
        return 0, None
    try:
        with MemoryStore._file_lock(path):
            records = _read_records(path, strict=True)
            hit = ForgottenIndex(records).contained(text)
            if not hit:
                return 0, None
            kept = [record for record in records if record["sha256"] not in hit]
            _write_records(path, kept)
            return len(records) - len(kept), None
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not clear forgotten records in %s", path.name, exc_info=True)
        return 0, (f"The text was saved, but clearing its forgotten record in {FORGOTTEN_FILENAME} failed ({exc}).")
