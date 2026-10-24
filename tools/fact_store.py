"""Bi-temporal structured fact store (DL-P2.9).

Small subject/predicate/content facts learned about the user, stored under
``<home>/memories/FACTS.json`` next to TOPICS.json with the same file lock and
atomic-replace write discipline. Every record is bi-temporal: ``valid_from``/
``valid_to`` bracket the interval during which the fact was believed current,
``superseded_by`` points at the replacing record, and superseded records are
NEVER physically deleted — history stays queryable
(``include_history=True`` / :func:`fact_history`).

Prior art reused: Mem0 (arXiv 2504.19413) ADD/UPDATE/DELETE/NOOP decision
phase; Zep/Graphiti (arXiv 2501.13956) bi-temporal valid_at/invalid_at edges —
invalidate instead of delete.

Store format (``{"version": 1, "facts": [...]}``) is forward-compatible: a
record missing the new fields loads unchanged as an open fact stamped with the
file's earliest known write time.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import get_hermes_home
from tools.memory_tool_store import MemoryStore, _scan_memory_content

logger = logging.getLogger(__name__)

_MAX_FACTS = 2000
_MAX_CONTENT_CHARS = 400
_MAX_SUBJECT_CHARS = 64
_VALID_ACTIONS = ("ADD", "UPDATE", "DELETE", "NOOP")

# Facts with a valid_to strictly before this instant are stale; comparisons are
# string-safe because all stamps are written by _now_iso()/ISO normalisation.


def fact_store_path() -> Path:
    """Profile-scoped FACTS.json path (resolved per call, like topic_memory_path)."""
    from tools.memory_tool import get_memory_dir

    return get_memory_dir() / "FACTS.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_subject(value: Any) -> str:
    """Stable subject key: lowercase, whitespace/underscore runs -> single hyphen."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^0-9a-z\-\u4e00-\u9fff]+", "", text)
    return text.strip("-")[:_MAX_SUBJECT_CHARS]


_CACHE_LOCK = threading.Lock()
_CACHE = {"key": None, "payload": None}  # (path, mtime_ns, size) -> parsed payload
_SIG_CACHE = {"key": None, "signatures": None, "df": None, "size": 0}


def _read_payload(*, strict: bool = False) -> dict[str, Any]:
    path = fact_store_path()
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        with _CACHE_LOCK:
            _CACHE["key"] = None
            _CACHE["payload"] = None
        return {"version": 1, "facts": []}
    except OSError:
        cache_key = None
    if cache_key is not None:
        with _CACHE_LOCK:
            if _CACHE["key"] == cache_key:
                return _CACHE["payload"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "facts": []}
    except (json.JSONDecodeError, OSError) as exc:
        # Corrupt/unreadable store: never crash a write path. Quarantine the
        # bad file (history is preserved for manual recovery) and start empty.
        quarantine = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        try:
            if path.exists():
                os.replace(path, quarantine)
                logger.warning("fact store unreadable (%s); quarantined to %s", exc, quarantine.name)
            else:
                logger.warning("fact store unreadable (%s); starting empty", exc)
        except OSError:
            logger.warning("fact store quarantine failed (%s); starting empty", exc)
        with _CACHE_LOCK:
            _CACHE["key"] = None
            _CACHE["payload"] = None
        return {"version": 1, "facts": []}
    if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
        logger.warning("fact store payload has an unexpected shape; starting empty")
        return {"version": 1, "facts": []}
    if cache_key is not None:
        with _CACHE_LOCK:
            _CACHE["key"] = cache_key
            _CACHE["payload"] = raw
    return raw


def _write_payload(payload: dict[str, Any]) -> None:
    path = fact_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(tmp, path)


def load_facts(*, include_history: bool = False) -> list[dict[str, Any]]:
    """Load fact records; by default only currently-valid ones (valid_to unset)."""
    facts = _read_payload()["facts"]
    if include_history:
        return list(facts)
    now = _now_iso()
    live = []
    for row in facts:
        valid_to = str(row.get("valid_to") or "").strip()
        if not valid_to or valid_to > now:
            live.append(row)
    return live


def _fact_signature(content: str) -> set[str]:
    """Character bigrams of CJK runs + lowercase latin/digit runs (reuse of the
    DL-P2.6/P2.8b lexical signal idea, kept local so fact_store has no import
    cycle with topic_memory_store)."""
    from tools.topic_memory_store import _signals

    return _signals(content)


def _norm_content(content: str) -> str:
    return "".join(ch.lower() for ch in str(content or "") if ch.isalnum())


def _same_fact(a: str, b: str) -> bool:
    """Exact same statement after normalisation (duplicate detection)."""
    return bool(a) and _norm_content(a) == _norm_content(b)


def _similar_fact(content: str, existing: str, *, threshold: float = 0.55) -> bool:
    """Lexical-overlap similarity for candidate matching (Jaccard over signals)."""
    sa, sb = _fact_signature(content), _fact_signature(existing)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold


def find_candidate_facts(
    content: str, *, subject: str = "", top_k: int = 5, include_history: bool = False
) -> list[dict[str, Any]]:
    """Top-k facts lexically similar to ``content`` (exact subject match first,
    then Jaccard similarity over content signals), best-first. Used by the
    pipeline decision step instead of a new retriever."""
    norm_subject = _normalize_subject(subject)
    pool = load_facts(include_history=include_history)
    sig_a = _fact_signature(content)
    scored = []
    for row in pool:
        sig_b = _fact_signature(str(row.get("content") or ""))
        if not sig_a or not sig_b:
            continue
        jaccard = len(sig_a & sig_b) / len(sig_a | sig_b)
        subject_bonus = 3.0 if norm_subject and str(row.get("subject") or "") == norm_subject else 0.0
        scored.append((jaccard + subject_bonus, jaccard, row))
    scored.sort(key=lambda item: (-item[0], -item[1], str(item[2].get("id") or "")))
    return [row for _total, _jacc, row in scored[:top_k]]


def _new_id() -> str:
    return f"f{int(time.time() * 1000):013d}-{os.getpid():05d}-{threading.get_ident() % 100000:05d}"


def add_fact(
    subject: str,
    predicate: str,
    content: str,
    *,
    confidence: float = 0.9,
    source: str = "",
    source_session_id: str = "",
    source_message_id: str = "",
    observed_at: str | None = None,
    valid_from: str | None = None,
) -> dict[str, Any]:
    """Insert a new open fact. Duplicate content under the same subject is a NOOP."""
    norm_subject = _normalize_subject(subject)
    clean_content = str(content or "").strip()
    if not norm_subject:
        return {"success": False, "error": "subject is required for a fact"}
    if not clean_content:
        return {"success": False, "error": "content is required for a fact"}
    if len(clean_content) > _MAX_CONTENT_CHARS:
        return {"success": False, "error": f"fact content too long ({len(clean_content)}/{_MAX_CONTENT_CHARS} chars)"}
    clean_predicate = str(predicate or "").strip()[:_MAX_SUBJECT_CHARS]
    threat = _scan_memory_content(f"{subject}\n{clean_predicate}\n{clean_content}")
    if threat:
        return {"success": False, "error": threat}
    stamp = _normalize_iso_or_none(observed_at) or _now_iso()
    valid_from_iso = _normalize_iso_or_none(valid_from) or stamp
    path = fact_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        facts = payload["facts"]
        for row in facts:
            if (str(row.get("subject") or "") == norm_subject
                    and _same_fact(clean_content, str(row.get("content") or ""))
                    and not str(row.get("valid_to") or "").strip()):
                return {
                    "success": True, "action": "NOOP", "target_id": row.get("id"),
                    "message": "identical open fact already stored",
                }
        if len(facts) >= _MAX_FACTS:
            return {"success": False, "error": f"fact store already has {_MAX_FACTS} records"}
        record = {
            "id": _new_id(),
            "subject": norm_subject,
            "predicate": clean_predicate,
            "content": clean_content,
            "confidence": round(float(confidence), 3),
            "source": str(source or "")[:200],
            "source_session_id": str(source_session_id or "")[:120],
            "source_message_id": str(source_message_id or "")[:120],
            "observed_at": stamp,
            "valid_from": valid_from_iso,
            "valid_to": None,
            "superseded_by": None,
        }
        facts.append(record)
        _write_payload({"version": 1, "facts": facts})
    return {
        "success": True, "action": "ADD", "target_id": record["id"],
        "message": "fact added",
    }


def _normalize_iso_or_none(value: Any) -> Optional[str]:
    parsed = _parse_iso(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def close_fact(
    fact_id: str,
    *,
    reason: str = "superseded",
    superseded_by: str | None = None,
    valid_to: str | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Set valid_to (close) a fact without deleting it. Idempotent."""
    path = fact_store_path()
    if not path.exists():
        return {"success": False, "error": "fact store does not exist"}
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        facts = payload["facts"]
        idx = next((i for i, row in enumerate(facts) if row.get("id") == fact_id), None)
        if idx is None:
            return {"success": False, "error": f"fact {fact_id} not found"}
        row = facts[idx]
        if str(row.get("valid_to") or "").strip():
            return {"success": True, "action": "NOOP", "target_id": fact_id,
                    "message": "fact already closed"}
        row["valid_to"] = _normalize_iso_or_none(valid_to) or _now_iso()
        row["closed_reason"] = str(reason or "superseded")[:64]
        if superseded_by:
            row["superseded_by"] = str(superseded_by)[:120]
        if source:
            row["closed_by_source"] = str(source)[:200]
        facts[idx] = row
        _write_payload({"version": 1, "facts": facts})
    return {"success": True, "action": "CLOSE", "target_id": fact_id,
            "message": "fact closed (history preserved)"}


def update_fact(
    fact_id: str,
    *,
    content: str | None = None,
    confidence: float | None = None,
    source: str = "",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Bi-temporal UPDATE: close the old record (valid_to=now, superseded_by=new id)
    and insert the new content as a fresh open record. The old record is never
    physically deleted; returns both ids."""
    path = fact_store_path()
    if not path.exists():
        return {"success": False, "error": "fact store does not exist"}
    clean_content = str(content or "").strip()
    if not clean_content:
        return {"success": False, "error": "content is required for an UPDATE"}
    if len(clean_content) > _MAX_CONTENT_CHARS:
        return {"success": False, "error": f"fact content too long ({len(clean_content)}/{_MAX_CONTENT_CHARS} chars)"}
    threat = _scan_memory_content(clean_content)
    if threat:
        return {"success": False, "error": threat}
    with MemoryStore._file_lock(path):
        payload = _read_payload(strict=True)
        facts = payload["facts"]
        idx = next((i for i, row in enumerate(facts) if row.get("id") == fact_id), None)
        if idx is None:
            return {"success": False, "error": f"fact {fact_id} not found"}
        old = facts[idx]
        if str(old.get("valid_to") or "").strip():
            return {"success": False, "error": f"fact {fact_id} is already closed; ADD a new fact instead"}
        if _same_fact(clean_content, str(old.get("content") or "")):
            return {"success": True, "action": "NOOP", "target_id": fact_id,
                    "message": "content unchanged"}
        stamp = _normalize_iso_or_none(observed_at) or _now_iso()
        new_record = {
            "id": _new_id(),
            "subject": str(old.get("subject") or ""),
            "predicate": str(old.get("predicate") or ""),
            "content": clean_content,
            "confidence": round(float(confidence if confidence is not None else old.get("confidence", 0.9)), 3),
            "source": str(source or old.get("source") or "")[:200],
            "source_session_id": str(old.get("source_session_id") or "")[:120],
            "source_message_id": str(old.get("source_message_id") or "")[:120],
            "observed_at": stamp,
            "valid_from": stamp,
            "valid_to": None,
            "superseded_by": None,
        }
        old["valid_to"] = stamp
        old["superseded_by"] = new_record["id"]
        old["closed_reason"] = "superseded"
        facts.append(new_record)
        _write_payload({"version": 1, "facts": facts})
    return {
        "success": True, "action": "UPDATE", "target_id": new_record["id"],
        "superseded_id": fact_id,
        "message": "fact superseded; history preserved",
    }


def fact_history(subject: str) -> list[dict[str, Any]]:
    """Full history (open + closed) for one subject, newest-first."""
    norm = _normalize_subject(subject)
    rows = [r for r in load_facts(include_history=True) if str(r.get("subject") or "") == norm]
    rows.sort(key=lambda r: (str(r.get("observed_at") or ""), str(r.get("id") or "")), reverse=True)
    return rows


def recall_facts(query: str, *, limit: int = 3, char_budget: int = 600) -> list[dict[str, Any]]:
    """Currently-valid facts lexically relevant to the query, best-first, within
    budget. History is NEVER served here (explicit ``fact_history``/``load_facts``
    only).

    Scoring is rarity-weighted (DL-P2.6 lesson): a query signal counts for the
    fact in inverse proportion to how many stored facts share it, so common
    particles (阿豪/现在/用) cannot outrank the one distinguishing value
    (iPhone/bash/米兰)."""
    clean = str(query or "").strip()
    if not clean or limit <= 0:
        return []
    sig_q = _fact_signature(clean)
    if not sig_q:
        return []
    path_key = fact_store_path()
    try:
        stat = path_key.stat()
        cache_key = (str(path_key), stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None
    with _CACHE_LOCK:
        if cache_key is not None and _SIG_CACHE["key"] == cache_key:
            content_sigs, df, store_size = (
                _SIG_CACHE["signatures"], _SIG_CACHE["df"], _SIG_CACHE["size"])
        else:
            rows = load_facts()
            content_sigs = {}
            for row in rows:
                sig_c = _fact_signature(str(row.get("content") or ""))
                if sig_c:
                    content_sigs[row.get("id")] = sig_c
            # Document frequency per signal across stored facts (min 1).
            df = {}
            for sig in content_sigs.values():
                for token in sig:
                    df[token] = df.get(token, 0) + 1
            store_size = max(1, len(content_sigs))
            if cache_key is not None:
                _SIG_CACHE["key"] = cache_key
                _SIG_CACHE["signatures"] = content_sigs
                _SIG_CACHE["df"] = df
                _SIG_CACHE["size"] = store_size
    scored = []
    for row in load_facts():
        sig_c = content_sigs.get(row.get("id"))
        if not sig_c:
            continue
        shared = sig_q & sig_c
        # The predicate carries the topic noun the content may omit ("手机",
        # "咖啡"): match the query against content + predicate jointly.
        sig_p = _fact_signature(str(row.get("predicate") or ""))
        shared_p = sig_q & sig_p if sig_p else set()
        if not shared and not shared_p:
            continue
        weight = sum(math.log(1 + store_size / df.get(token, 1)) for token in shared)
        weight += 0.5 * sum(1 for _ in shared_p)
        subject = str(row.get("subject") or "")
        sig_s = _fact_signature(subject.replace("-", " "))
        if sig_q & sig_s:
            weight += 1.0
        scored.append((weight, row))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    selected, used = [], 0
    for score, row in scored:
        content = str(row.get("content") or "")
        cost = len(content) + 24
        if used + cost > char_budget:
            continue
        selected.append({**row, "score": round(score, 3)})
        used += cost
        if len(selected) >= limit:
            break
    return selected


def format_fact_recall(facts: Iterable[dict[str, Any]]) -> str:
    """Render selected facts as a compact sidecar block (parseable, no headers
    that would collide with topic ``###`` parsing)."""
    rows = list(facts)
    if not rows:
        return ""
    lines = ["Current facts about the user (structured store; superseded values are history only):"]
    for row in rows:
        note_bits = []
        if row.get("observed_at"):
            note_bits.append(f"observed {str(row['observed_at'])[:10]}")
        if row.get("confidence") is not None:
            note_bits.append(f"conf {row['confidence']}")
        note = f" ({'; '.join(note_bits)})" if note_bits else ""
        lines.append(f"- {row.get('content')}{note}")
    return "\n".join(lines)


def reset_fact_store() -> bool:
    """Delete the fact store file. Returns whether it existed."""
    path = fact_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        if path.exists():
            path.unlink()
            return True
    return False


def fact_pipeline_enabled() -> bool:
    """Return the ``memory.fact_pipeline`` opt-in flag (default OFF).

    Read lazily through the same config path as ``topic_provenance_enabled``
    (DL-P2.7) / ``hybrid_recall`` (DL-P2.8b). OFF keeps writes and recall
    byte-identical to the pre-flag behaviour: the extraction pipeline does not
    run and recall output is unchanged.
    """
    try:
        from tools.memory_tool import get_builtin_memory_config

        section = get_builtin_memory_config()
    except Exception:
        logger.debug("Could not read memory config for fact pipeline", exc_info=True)
        return False
    from utils import is_truthy_value

    return is_truthy_value(section.get("fact_pipeline"), default=False)


__all__ = [
    "fact_store_path", "load_facts", "add_fact", "update_fact", "close_fact",
    "fact_history", "recall_facts", "format_fact_recall", "find_candidate_facts",
    "reset_fact_store", "fact_pipeline_enabled",
]
