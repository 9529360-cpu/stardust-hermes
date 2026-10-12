"""Forgotten-text records: text the owner removed, kept so recall cannot bring it back.

``memories/forgotten.json`` sits beside MEMORY.md and TOPICS.json. Every path that removes a
memory entry, a topic summary or a learning-graph card appends the removed text here; a reset
records what it erased. Recall filters on these records: session_search results and topic
recall results that contain a recorded phrase are dropped. Transcripts are never edited or
deleted, and the frozen prompt snapshot is not touched.

Matching is verbatim after Unicode-compatibility, case and whitespace folding, so a paraphrase
in an old chat is not filtered. Phrases shorter than ``MIN_PHRASE_CHARS`` are recorded but never
used to filter, so a two-character entry cannot hide unrelated results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional
import unicodedata

from utils import atomic_write_text

logger = logging.getLogger(__name__)

FORGOTTEN_FILENAME = "forgotten.json"
MIN_PHRASE_CHARS = 4


def fold_text(value: Any) -> str:
    """Comparison form shared by every forget and recall match."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def forgotten_path(memories_dir: Path | None = None) -> Path:
    """The forgotten-records file beside the memories of *memories_dir* (default: the active profile)."""
    if memories_dir is not None:
        return memories_dir / FORGOTTEN_FILENAME
    from tools import memory_tool  # resolved per call: profiles and tests redirect the memories dir

    return memory_tool.get_memory_dir() / FORGOTTEN_FILENAME


def _malformed(strict: bool, problem: str) -> List[dict]:
    """Strict callers refuse (so a write never replaces bytes it could not parse); tolerant readers warn and
    treat the file as empty, which only stops recall from filtering."""
    if strict:
        raise RuntimeError(f"{FORGOTTEN_FILENAME} {problem}; refusing to overwrite it")
    logger.warning("Ignoring malformed %s (%s); recall is not filtering forgotten text", FORGOTTEN_FILENAME, problem)
    return []


def _read_records(path: Path, *, strict: bool) -> List[dict]:
    """Records stored in *path*."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        logger.warning("Could not read %s; recall is not filtering forgotten text", path.name, exc_info=True)
        return []
    except UnicodeDecodeError:
        return _malformed(strict, "is not valid UTF-8")
    try:
        data = json.loads(raw)
    except ValueError:
        return _malformed(strict, "is not valid JSON")
    rows = data.get("forgotten") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return _malformed(strict, "has an invalid forgotten payload")
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("text"), str)]


def forgotten_store_error() -> Optional[str]:
    """Why forgetting cannot be recorded right now, or None. Checked BEFORE an entry is removed,
    so nothing is removed without its record."""
    try:
        _read_records(forgotten_path(), strict=True)
    except (OSError, RuntimeError) as exc:
        return (f"Refusing to forget: {FORGOTTEN_FILENAME} cannot be read ({exc}). Repair or move that "
                "file so forgotten text can still be filtered from recall, then retry. Nothing was removed.")
    return None


def record_forgotten(texts: Iterable[str], *, source: str) -> Optional[str]:
    """Append *texts* to the forgotten records, keeping the first record for a repeated text.

    Returns None on success, or a warning when the record could not be written. The removal it
    describes has already happened, so this reports instead of raising.
    """
    from tools.memory_tool_store import MemoryStore  # lazy: memory_tool_store imports this module

    clean: List[str] = []
    seen: set = set()
    for text in texts:
        if isinstance(text, str) and text.strip() and fold_text(text) not in seen:
            seen.add(fold_text(text))
            clean.append(text.strip())
    if not clean:
        return None
    path = forgotten_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with MemoryStore._file_lock(path):
            records = _read_records(path, strict=True)
            known = {fold_text(row["text"]) for row in records}
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            added = [text for text in clean if fold_text(text) not in known]
            if not added:
                return None
            records.extend({"text": text, "forgotten_at": stamp, "source": source} for text in added)
            atomic_write_text(
                path,
                json.dumps({"version": 1, "forgotten": records}, ensure_ascii=False, indent=2) + "\n",
                tmp_prefix=".forgotten.",
                preserve_mode=True,
                create_mode=0o600,
                fsync_dir=True,
            )
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not record forgotten text in %s", path.name, exc_info=True)
        return (f"The entry was removed, but recording it in {FORGOTTEN_FILENAME} failed ({exc}); "
                "old chats may still surface it in recall until that is repaired.")
    return None


def forgotten_phrases(memories_dir: Path | None = None) -> List[str]:
    """Folded phrases that recall filters on (tolerant read)."""
    phrases: List[str] = []
    for row in _read_records(forgotten_path(memories_dir), strict=False):
        phrase = fold_text(row["text"])
        if len(phrase) >= MIN_PHRASE_CHARS and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def mentions_forgotten(text: Any, phrases: List[str]) -> bool:
    """Whether *text* contains any recorded phrase."""
    if not phrases:
        return False
    folded = fold_text(text)
    return any(phrase in folded for phrase in phrases)
