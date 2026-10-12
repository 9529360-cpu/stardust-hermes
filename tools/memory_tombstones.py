"""Forgotten-text records: what the owner removed, kept as hashes so recall and writes can be checked.

``memories/forgotten.json`` sits beside MEMORY.md and TOPICS.json. A record holds the SHA-256 of the normalized
forgotten text, that text's normalized length (needed to find it inside a longer passage), the time, the source
store, the scope (``entry``, ``sentence``, ``keyword`` or ``topic``) and, for topic text, the SHA-256 of the topic
key. Neither the text nor the topic key is stored. Plaintext rows from the earlier build are hashed when read, and
the file is written back without them.

Normalization (``fold_text``): Unicode NFKC, then casefold, then runs of whitespace collapsed to one space. Matching is
verbatim after that: a paraphrase is not matched. An ASCII phrase must start and end at a word boundary, so "Coffee"
does not match inside "coffeemaker". CJK text has no spaces and matches wherever it occurs.

- Recall (session_search and topic recall) drops a result whose normalized text contains a recorded phrase. When the
  file cannot be read, recall is suspended rather than unfiltered. A passage too large to check within MATCH_BUDGET is
  dropped as well.
- Writes that do not come from the owner (background review, staged proposals, agent import, the agent's own adds)
  refuse text that contains a recorded phrase, and refuse a topic whose whole-topic record matches. An unreadable
  file refuses every memory write. A proposal is also refused when a phrase it would remove or replace contains a
  recorded phrase, so the pending list never holds forgotten text.
- An owner's own add clears a record only when one of its entries equals the forgotten text exactly. Containment does
  not clear, so re-adding a longer sentence leaves the record in place.
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
REFUSAL_PHRASE = (
    "The phrase to change repeats something the owner forgot. Nothing was staged, so forgotten text is not written to "
    "the pending list."
)
REFUSAL_NAME = (
    "The topic name or key repeats something the owner forgot, so this topic cannot be used here. Nothing was changed."
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
# Work one passage may cost (see ForgottenIndex._scan), in fingerprint checks: about 1.3 microseconds each, so about
# 0.5 s at the limit. A passage over it is not scanned: it counts as mentioning forgotten text, so recall drops it and
# writes refuse it. Records written before fingerprints need a SHA-256 per window, which costs DIGEST_CHECK_COST checks.
MATCH_BUDGET = 400_000
DIGEST_CHECK_COST = 4
_FP_MOD = (1 << 61) - 1  # Mersenne prime: the modulus of the window fingerprints stored with each record
_FP_BASE = 1_000_000_007


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
        if isinstance(row.get("fp"), int) and 0 <= row["fp"] < _FP_MOD:
            record["fp"] = row["fp"]
        legacy = False
    elif isinstance(row.get("text"), str):
        folded = fold_text(row["text"])
        if not folded:
            return None, True
        record = {"sha256": _sha(folded), "length": len(folded), "fp": _fingerprint(folded)}
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
            record: Dict[str, Any] = {"sha256": digest, "length": len(folded), "fp": _fingerprint(folded),
                                      "forgotten_at": stamp, "source": source, "scope": scope}
            if topic_key:
                record["topic_sha256"] = topic_digest(topic_key)
            records.append(record)
            added += 1
        if added:
            _write(path, records)


def _fingerprint(folded: str) -> int:
    """Polynomial fingerprint of a folded text, modulo _FP_MOD. Stored with each record. Any window's fingerprint follows
    from prefix values in constant time (see _prefix_fingerprints), so the scan needs no digest per window; a fingerprint
    hit is then confirmed with SHA-256."""
    value = 0
    for ch in folded:
        value = (value * _FP_BASE + ord(ch)) % _FP_MOD
    return value


def _prefix_fingerprints(folded: str) -> List[int]:
    """prefix[i] is the fingerprint of folded[:i]. The fingerprint of folded[s:e] is then (prefix[e] - prefix[s] * B**(e-s))
    modulo _FP_MOD, which equals _fingerprint of that window."""
    prefix = [0]
    value = 0
    for ch in folded:
        value = (value * _FP_BASE + ord(ch)) % _FP_MOD
        prefix.append(value)
    return prefix


def _is_word(ch: str) -> bool:
    """An ASCII letter, digit or underscore. Word boundaries are enforced only around these. CJK and other scripts have
    no spaces between words, so a recorded phrase in them matches wherever it occurs."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _boundary_bits(folded: str) -> Tuple[int, int]:
    """Bit masks over *folded* (bit i is position i) marking where a recorded text may start and where it may end. A text
    never starts or ends on a space. An ASCII word character may not touch another word character on the outside, so
    "coffee" does not match inside "coffeemaker"."""
    size = len(folded)
    word = [_is_word(ch) for ch in folded]
    starts: List[str] = []
    ends: List[str] = []
    for i in range(size - 1, -1, -1):  # highest position first, so that int(text, 2) puts bit i at position i
        if folded[i] == " ":
            starts.append("0")
            ends.append("0")
            continue
        starts.append("0" if word[i] and i > 0 and word[i - 1] else "1")
        ends.append("0" if word[i] and i + 1 < size and word[i + 1] else "1")
    return int("".join(starts), 2), int("".join(ends), 2)


def _set_bit_positions(bits: int) -> List[int]:
    """Positions of the set bits of *bits*, highest first."""
    text = format(bits, "b")
    width = len(text)
    positions: List[int] = []
    at = text.find("1")
    while at != -1:
        positions.append(width - 1 - at)
        at = text.find("1", at + 1)
    return positions


class ForgottenIndex:
    """Recorded texts indexed by length, for finding one inside a passage.

    A passage is scanned once per distinct record length, and only at the candidate positions that pass the boundary tests
    (see _boundary_bits). A record with a stored fingerprint is found by comparing window fingerprints, and a match is
    confirmed with SHA-256. A record without one (written before fingerprints existed) costs a SHA-256 per candidate
    window. The cost of one scan is limited to MATCH_BUDGET: a passage over it is not scanned and counts as mentioning
    forgotten text. ``mentions`` caches its answer per passage and index.
    """

    _mention_cache: "OrderedDict[Tuple[str, str], bool]" = OrderedDict()
    _mention_lock = threading.Lock()

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._fp_tables: Dict[int, Dict[int, Set[bytes]]] = {}  # length -> fingerprint -> digests
        self._digest_only: Dict[int, Set[bytes]] = {}  # length -> digests of records without a fingerprint
        self._topic_blocks: Set[str] = set()
        entries: List[Tuple[str, int, int]] = []
        for record in records:
            if record["length"] >= MIN_PHRASE_CHARS:
                digest = bytes.fromhex(record["sha256"])
                if "fp" in record:
                    self._fp_tables.setdefault(record["length"], {}).setdefault(record["fp"], set()).add(digest)
                else:
                    self._digest_only.setdefault(record["length"], set()).add(digest)
                entries.append((record["sha256"], record["length"], record.get("fp", -1)))
            if record.get("scope") == "topic" and record.get("topic_sha256"):
                self._topic_blocks.add(record["topic_sha256"])
        self.fingerprint = hashlib.sha256(
            json.dumps([sorted(entries), sorted(self._topic_blocks)]).encode("utf-8")).hexdigest()

    def __bool__(self) -> bool:
        return bool(self._fp_tables) or bool(self._digest_only) or bool(self._topic_blocks)

    def blocks_topic(self, key: Any) -> bool:
        """Whether a whole-topic record matches this topic key (compared by hash)."""
        return topic_digest(key) in self._topic_blocks

    def _scan(self, folded: str, *, first_only: bool) -> Optional[Set[bytes]]:
        """Digests of the recorded texts found in *folded* at the boundaries of _boundary_bits, or None when the scan would
        cost more than MATCH_BUDGET (the caller then fails closed)."""
        size = len(folded)
        lengths = sorted(length for length in set(self._fp_tables) | set(self._digest_only) if length <= size)
        if not lengths:
            return set()
        starts, ends = _boundary_bits(folded)
        # Bit e of candidates[length] is set when a text of that length may both start at e - length + 1 and end at e.
        candidates = {length: (starts << (length - 1)) & ends for length in lengths}
        fingerprint_checks = sum(candidates[length].bit_count() for length in lengths if length in self._fp_tables)
        digest_checks = sum(candidates[length].bit_count() for length in lengths if length in self._digest_only)
        if fingerprint_checks + DIGEST_CHECK_COST * digest_checks > MATCH_BUDGET:
            return None
        prefix = _prefix_fingerprints(folded) if fingerprint_checks else []
        found: Set[bytes] = set()
        for length in lengths:
            bits = candidates[length]
            if not bits:
                continue
            if length in self._fp_tables:
                table, scale = self._fp_tables[length], pow(_FP_BASE, length, _FP_MOD)
                for end in _set_bit_positions(bits):
                    start = end - length + 1
                    for digest in table.get((prefix[end + 1] - prefix[start] * scale) % _FP_MOD, ()):
                        if hashlib.sha256(folded[start:end + 1].encode("utf-8")).digest() == digest:
                            found.add(digest)
                            if first_only:
                                return found
            if length in self._digest_only:
                wanted = self._digest_only[length]
                for end in _set_bit_positions(bits):
                    digest = hashlib.sha256(folded[end - length + 1:end + 1].encode("utf-8")).digest()
                    if digest in wanted:
                        found.add(digest)
                        if first_only:
                            return found
        return found

    def mentions(self, text: Any) -> bool:
        """Whether *text* contains a recorded text at the boundaries described above. A passage too large to check
        counts as mentioning one."""
        if not (self._fp_tables or self._digest_only):
            return False
        folded = fold_text(text)
        key = (self.fingerprint, _sha(folded))
        with ForgottenIndex._mention_lock:
            cached = ForgottenIndex._mention_cache.get(key)
        if cached is not None:
            return cached
        found = self._scan(folded, first_only=True)
        result = True if found is None else bool(found)
        with ForgottenIndex._mention_lock:
            ForgottenIndex._mention_cache[key] = result
            while len(ForgottenIndex._mention_cache) > _MENTION_CACHE_SIZE:
                ForgottenIndex._mention_cache.popitem(last=False)
        return result


_INDEX_CACHE: Dict[str, Tuple[str, bool, ForgottenIndex]] = {}  # path -> (content digest, strict, index)


def forgotten_index(memories_dir: Optional[Path] = None, *, strict: bool = False) -> ForgottenIndex:
    """Index of the recorded texts. Strict callers get OSError or RuntimeError for an unreadable file. A lenient read skips
    unusable rows (and reads an unparseable file as empty), so only a strict read may filter or refuse: a strict call is
    never answered from a lenient read of the same bytes."""
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
    if cached is not None and cached[0] == content and (cached[1] or not strict):
        return cached[2]
    records, _legacy = _load(path, strict=strict)
    index = ForgottenIndex(records)
    _INDEX_CACHE[str(path)] = (content, strict, index)
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


def _as_strings(value: Any) -> List[str]:
    """The strings in a payload field that may hold one string or a list of them."""
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value] if isinstance(value, list) else []


def _phrase_refusal(phrase: str, memories_dir: Optional[Path]) -> Optional[str]:
    """Refusal for a phrase a proposal would persist, such as the text it removes or replaces. A phrase that contains a
    forgotten text is refused, so the pending list never holds it. A phrase that is only part of a forgotten text cannot
    be seen, because the records hold hashes; that limit is documented."""
    if not phrase:
        return None
    try:
        index = forgotten_index(memories_dir, strict=True)
    except (OSError, RuntimeError) as exc:
        return REFUSAL_UNREADABLE.format(problem=exc)
    return REFUSAL_PHRASE if index.mentions(phrase) else None


def name_refusal(names: Iterable[str], memories_dir: Optional[Path] = None) -> Optional[str]:
    """Refusal for a topic name or key (any of *names*) that repeats forgotten text, for a forget. The name is part of the
    topic's stored text, so a forget may not carry forgotten text into its own request either. An unreadable file refuses."""
    wanted = [name for name in names if name]
    if not wanted:
        return None
    try:
        index = forgotten_index(memories_dir, strict=True)
    except (OSError, RuntimeError) as exc:
        return REFUSAL_UNREADABLE.format(problem=exc)
    return REFUSAL_NAME if any(index.mentions(name) for name in wanted) else None


def proposal_refusal(payload: Dict[str, Any], memories_dir: Optional[Path] = None) -> Optional[str]:
    """Refusal for a staged memory proposal. A proposal is written to the pending list whole, so it is refused when any
    text it would persist repeats forgotten text: the phrases it removes or replaces (old_text, single or in a batch), a
    topic's name, key, title and keywords, and the text it would add (a memory add or replace, or a topic's name, key,
    title, keywords and summary under its topic key). Proposals are never owner-initiated, so the owner adds such text
    directly."""
    phrases = [payload.get("old_text") or ""]
    phrases += [op.get("old_text") or "" for op in payload.get("operations") or [] if isinstance(op, dict)]
    key, candidate = "", ""
    if payload.get("target") == "topic":
        from tools.topic_memory_store import topic_candidate  # lazy: topic_memory_store imports this module

        key, candidate = topic_candidate(payload.get("topic") or payload.get("title") or "", payload.get("content") or "",
                                         title=payload.get("title"), keywords=payload.get("keywords"))
        phrases += [*_as_strings(payload.get("topic")), key, *_as_strings(payload.get("title")),
                    *_as_strings(payload.get("keywords"))]
    for phrase in phrases:
        if reason := _phrase_refusal(phrase, memories_dir):
            return reason
    if payload.get("target") == "topic":
        if payload.get("action") not in ("upsert", "add", "replace"):
            return None
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


def clear_forgotten_for(texts: Iterable[str], *, topic_key: Optional[str] = None,
                        memories_dir: Optional[Path] = None) -> int:
    """After an owner's own write: drop the records whose text equals one of *texts* exactly once normalized, and the
    whole-topic record for *topic_key*. Containment does not clear: an entry that merely contains a forgotten phrase
    leaves its record in place. Raises when the file cannot be written. Returns the number of records dropped."""
    path = forgotten_path(memories_dir)
    if not path.exists():
        return 0
    exact = {text_digest(text) for text in texts if fold_text(text)}
    topic = topic_digest(topic_key) if topic_key else None
    if not exact and topic is None:
        return 0
    with _lock(path):
        records, _legacy = _load(path, strict=True)
        kept = [record for record in records if record["sha256"] not in exact and not (
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
