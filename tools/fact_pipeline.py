"""Post-conversation fact extraction pipeline (DL-P2.9).

Runs INSIDE the existing background-review thread after a conversation, only
when ``memory.fact_pipeline`` is ON (default OFF). One auxiliary model call
extracts candidate facts from the conversation tail as JSON; each candidate is
then decided deterministically against the top-k similar currently-valid facts
in :mod:`tools.fact_store` — ADD / UPDATE / DELETE / NOOP (Mem0's two-phase
shape, arXiv 2504.19413). UPDATE/DELETE only CLOSE the old record
(valid_to=now, superseded_by=new id) — never a physical delete (Zep/Graphiti
bi-temporal discipline, arXiv 2501.13956).

Failure discipline: malformed model JSON is logged and skipped (the store is
never touched); a model/provider failure skips the whole pipeline silently —
the conversation outcome is unaffected. The user-visible reply latency is
unchanged (background thread).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM = (
    "You extract durable facts about the user from a conversation. Return ONLY "
    "a JSON object {\"facts\": [{\"subject\": str, \"predicate\": str, \"content\": str, "
    "\"confidence\": float, \"retracted\": bool}]}. subject = stable snake_case key of the "
    "domain (e.g. residence, job, preferred_model). content = one short sentence stating "
    "the CURRENT value in the user's own language. retracted = true only when the user "
    "says an earlier statement no longer holds without giving a new value. Skip transient "
    "task details, secrets, and anything about third parties. Max 8 facts."
)

_MAX_FACTS_PER_RUN = 8
_MAX_CONVERSATION_CHARS = 12000
# Jaccard threshold over lexical signals for "same fact, new value" matching.
_UPDATE_SIMILARITY = 0.35
# Cross-subject candidates must be near-identical statements to supersede.
_CROSS_SUBJECT_FLOOR = 0.60
# Below this similarity (and no subject match) a candidate is a new fact.
_ADD_FLOOR = 0.20


def _conversation_text(messages: List[Dict]) -> str:
    parts: List[str] = []
    used = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        text = text.strip()
        if not text:
            continue
        parts.append(f"{message.get('role')}: {text}")
        used += len(text)
        if used >= _MAX_CONVERSATION_CHARS:
            break
    # Newest turns matter most; keep the tail if over budget.
    joined = "\n".join(parts)
    if len(joined) > _MAX_CONVERSATION_CHARS:
        joined = joined[-_MAX_CONVERSATION_CHARS:]
    return joined


def _parse_model_facts(raw: Any) -> List[Dict[str, Any]]:
    """Tolerantly parse the model's JSON reply; malformed output -> [] (skip)."""
    text = ""
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, dict):
        return _validate_fact_items(raw.get("facts"))
    elif raw is not None:
        # Some adapters return a completion object; take .content defensively.
        text = str(getattr(raw, "content", "") or "").strip()
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.info("fact pipeline: unparseable extraction JSON skipped")
        return []
    if not isinstance(parsed, dict):
        return []
    return _validate_fact_items(parsed.get("facts"))


def _validate_fact_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    clean = []
    for item in items[:_MAX_FACTS_PER_RUN]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        content = str(item.get("content") or "").strip()
        if not subject or not content:
            continue
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.9))))
        except (TypeError, ValueError):
            confidence = 0.9
        clean.append({
            "subject": subject,
            "predicate": str(item.get("predicate") or "").strip(),
            "content": content[:400],
            "confidence": confidence,
            "retracted": bool(item.get("retracted", False)),
        })
    return clean


def _jaccard(a: str, b: str) -> float:
    from tools.fact_store import _fact_signature

    sa, sb = _fact_signature(a), _fact_signature(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def decide_fact_ops(
    candidates: List[Dict[str, Any]],
    *,
    source: str = "fact-pipeline",
    source_session_id: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Pure decision layer: candidates -> store operations (no model calls).

    Each candidate is compared against the top-k similar currently-valid facts
    (subject match first, Jaccard similarity second — the existing lexical
    recall signal, no new retriever):

    - identical open content under the same subject -> NOOP (duplicate guard)
    - ``retracted`` and a close match -> DELETE (close the old record only)
    - similar enough to an existing fact but different content -> UPDATE
      (close old valid_to=now + superseded_by=new id, insert new record)
    - otherwise -> ADD
    """
    from tools import fact_store

    ops: List[Dict[str, Any]] = []
    for cand in candidates:
        subject = str(cand.get("subject") or "").strip()
        content = str(cand.get("content") or "").strip()
        if not subject or not content:
            continue
        similar = fact_store.find_candidate_facts(
            content, subject=subject, top_k=top_k,
        )
        exact = None
        best = None
        best_score = 0.0
        for row in similar:
            same_subject = str(row.get("subject") or "") == fact_store._normalize_subject(subject)
            score = _jaccard(content, str(row.get("content") or ""))
            if same_subject and fact_store._same_fact(content, str(row.get("content") or "")):
                exact = row
                break
            rank = score + (3.0 if same_subject else 0.0)
            if rank > best_score:
                best, best_score = row, rank
        if exact is not None:
            ops.append({"action": "NOOP", "subject": subject, "content": content,
                        "target_id": exact.get("id"), "reason": "duplicate"})
            continue
        if best is not None:
            same_subject = str(best.get("subject") or "") == fact_store._normalize_subject(subject)
            similarity = _jaccard(content, str(best.get("content") or ""))
            # Same subject = same fact chain: a different value under the same
            # stable domain key is a supersede by definition (short Chinese
            # statements differ by one 2-char value, so lexical similarity is
            # naturally low — a threshold would miss real updates).
            if cand.get("retracted"):
                if same_subject or similarity >= _CROSS_SUBJECT_FLOOR:
                    ops.append({"action": "DELETE", "subject": subject, "content": content,
                                "target_id": best.get("id"), "reason": "retracted"})
                    continue
            if same_subject:
                ops.append({"action": "UPDATE", "subject": subject, "content": content,
                            "target_id": best.get("id"),
                            "confidence": cand.get("confidence", 0.9), "reason": "superseded"})
                continue
            # Cross-subject lexical overlap (two facts sharing "阿豪"/"米兰") is
            # NOT a supersede: require near-identical statements (>=0.60).
            if similarity >= _CROSS_SUBJECT_FLOOR:
                ops.append({"action": "UPDATE", "subject": subject, "content": content,
                            "target_id": best.get("id"),
                            "confidence": cand.get("confidence", 0.9), "reason": "superseded"})
                continue
        if not cand.get("retracted"):
            ops.append({"action": "ADD", "subject": subject,
                        "predicate": cand.get("predicate", ""),
                        "content": content,
                        "confidence": cand.get("confidence", 0.9), "reason": "new"})
    return ops


def apply_fact_ops(
    ops: List[Dict[str, Any]],
    *,
    source: str = "fact-pipeline",
    source_session_id: str = "",
    source_message_id: str = "",
    observed_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply decided ops through the store API; every result reports success."""
    from tools import fact_store

    results = []
    for op in ops:
        action = op.get("action")
        try:
            if action == "ADD":
                res = fact_store.add_fact(
                    subject=op["subject"], predicate=op.get("predicate", ""),
                    content=op["content"], confidence=op.get("confidence", 0.9),
                    source=source, source_session_id=source_session_id,
                    source_message_id=source_message_id, observed_at=observed_at,
                )
            elif action == "UPDATE":
                res = fact_store.update_fact(
                    op["target_id"], content=op["content"],
                    confidence=op.get("confidence"), source=source,
                    observed_at=observed_at,
                )
            elif action == "DELETE":
                res = fact_store.close_fact(
                    op["target_id"], reason="retracted", source=source,
                )
            else:
                res = {"success": True, "action": "NOOP", "target_id": op.get("target_id")}
        except Exception as exc:  # store-level failure must not abort the batch
            logger.warning("fact pipeline op %s failed: %s", action, exc)
            res = {"success": False, "action": action, "error": str(exc)}
        results.append(res)
    return results


def run_fact_extraction(
    messages: List[Dict],
    *,
    source_session_id: str = "",
    call_llm=None,
) -> Dict[str, Any]:
    """Full pipeline for one finished conversation.

    Returns a small audit dict; never raises. ``call_llm`` is injectable for
    tests (production: ``agent.auxiliary_client.call_llm`` with task
    ``memory_fact_extract``).
    """
    from tools.fact_store import fact_pipeline_enabled

    if not fact_pipeline_enabled():
        return {"skipped": "flag-off"}
    from tools.fact_selectivity import enabled, extract

    if enabled():
        return extract(messages, source_session_id=source_session_id, call_llm=call_llm)
    text = _conversation_text(messages)
    if not text:
        return {"skipped": "empty-conversation"}
    try:
        if call_llm is None:
            from agent.auxiliary_client import call_llm as _call_llm

            call_llm = _call_llm
        reply = call_llm(
            task="memory_fact_extract",
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": f"Conversation:\n{text}\n\nExtract facts."},
            ],
            temperature=0.0,
            max_tokens=800,
        )
    except Exception as exc:
        logger.info("fact pipeline: model unavailable, skipped (%s)", exc)
        return {"skipped": "model-unavailable"}
    candidates = _parse_model_facts(reply)
    if not candidates:
        return {"skipped": "no-facts", "raw_preview": str(reply)[:200]}
    ops = decide_fact_ops(candidates, source_session_id=source_session_id)
    results = apply_fact_ops(
        ops, source="fact-pipeline", source_session_id=source_session_id,
    )
    counts: Dict[str, int] = {}
    for res in results:
        if res.get("success"):
            counts[res.get("action", "?")] = counts.get(res.get("action", "?"), 0) + 1
    return {"facts_seen": len(candidates), "ops": ops, "results": results, "counts": counts}


__all__ = [
    "run_fact_extraction", "decide_fact_ops", "apply_fact_ops", "_parse_model_facts",
]
