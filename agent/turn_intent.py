"""Opt-in per-turn intent sidecar (DL-P2.8d).

Rule-based extraction of the user's current-turn intent and explicit
constraints (must / must-not / scope / ordering / format / priority),
Chinese-first with English fallbacks. Zero model calls on the default
path; the optional ``agent.turn_intent_llm`` variant makes ONE auxiliary
call returning the same JSON shape and falls back to the rules card on
timeout or parse failure.

The rendered block rides the existing per-turn user-message sidecar
(next to the memory context, never the system prompt), so the byte-stable
system prompt and flag-off behaviour are preserved:

- ``agent.turn_intent``      (default OFF) — rules extraction + injection.
- ``agent.turn_intent_llm``  (default OFF) — aux-model extraction + injection;
  usable alone (single-flag A/B) or together with ``agent.turn_intent``.

Both flags fail closed on config errors and default to OFF, mirroring
``agent/task_checkpoint.py::enabled``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BLOCK_HEADER = "[Turn intent — extracted from THIS turn's user message; honor every constraint]"
_KIND_LABELS = {
    "must-not": "禁止",
    "must": "必须",
    "scope": "范围",
    "ordering": "顺序",
    "format": "格式",
    "direct": "直接",
    "priority": "优先",
}
_KIND_ORDER = {kind: i for i, kind in enumerate(
    ("must-not", "must", "priority", "ordering", "scope", "format", "direct"))}

_MAX_INTENT_CHARS = 120
_MAX_CONSTRAINTS = 8
_MAX_CONSTRAINT_CHARS = 90
_MAX_UNKNOWN = 3
_MAX_BLOCK_CHARS = 400
_RECENT_USER_TURNS = 3
_LLM_TIMEOUT_SECONDS = 8.0
_LLM_MAX_TOKENS = 400

# ── constraint cues (Chinese-first, English fallback) ───────────────────
# (compiled regex over ONE clause, kind). A cue hit records the smallest
# comma-segment containing it, so short imperative tails like
# "直接改，不用问我" yield two precise constraints instead of one blob.

_CUES: Tuple[Tuple[re.Pattern, str], ...] = (
    # clause/segment-initial 别 only: 错别字/别人/别墅 are not prohibitions
    (re.compile(r"(?:^|(?<=[，,、。；;！!？?\n\s]))别(?!人|墅|字)"
                r"|不要|不能|不可|绝不|千万别|无需|不用|不准|禁止|避免"
                r"|don'?t|do\s+not|never|avoid", re.IGNORECASE), "must-not"),
    (re.compile(r"必须|务必|一定要|确保|得先|得以|要保持|保持"
                r"|must\b|always\b|make\s+sure|ensure", re.IGNORECASE), "must"),
    (re.compile(r"以[^。，；！？\n]{1,20}为准|按[^。，；！？\n]{1,20}为准|优先", re.IGNORECASE), "priority"),
    (re.compile(r"先(?=确认|查|看|定位|跑|修|测|列|核对|检查|验证|问|分析|拆|清)"
                r"|(?:改|做|查|跑|写|测|确认|核对|验证)完"
                r"|然后|之后再|first[^。，；\n]{0,40}then", re.IGNORECASE), "ordering"),
    (re.compile(r"只用|只用|仅|只以|只能|不超过|最多|至少|不迟于|不晚于|以内|之内|上限"
                r"|\d+\s*字|under\s+\d+|\bonly\b|no\s+more\s+than\s+\d+|at\s+most\s+\d+", re.IGNORECASE), "scope"),
    (re.compile(r"用中文|中文回答|用英文|英文回答|表格|列表|纯文本|格式"
                r"|(?<![.\w])(?:markdown|json|yaml)(?![\w.])"
                r"|in\s+chinese|in\s+english", re.IGNORECASE), "format"),
    (re.compile(r"直接|立刻|马上|现在就|今天就|不用问|不用等|不用确认|不需要确认|别问"
                r"|just\s+do|right\s+away|straight\s+away|don'?t\s+ask", re.IGNORECASE), "direct"),
)

_QUESTION_RE = re.compile(r"[?？]|哪|什么|怎么|如何|多少|是否|到底")
_PRIOR_REF_RE = re.compile(r"我们之前|上次|刚才|此前|定好的|说好的|previously|last time")
_VERB_RE = re.compile(
    r"改|查|看|确认|修|删|写|生成|总结|继续|安排|开|拆|清|核对|迁移|导入|报告|盘点|收尾|跑|推|发|给|列|检查|验证|提醒|回执"
    r"|review|check|fix|run|list|create|write|update|delete|send|continue|confirm|clean", re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])")
_CLAUSE_SPLIT_RE = re.compile(r"[。；;！!？?\n]")
_SEGMENT_SPLIT_RE = re.compile(r"[，,、：:]")


def _text_of(message: Any) -> str:
    """Best-effort plain text of a user message (str or multimodal list)."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, (list, tuple)):
        parts = []
        for part in message:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return " ".join(parts)
    if isinstance(message, dict):
        content = message.get("content")
        return content if isinstance(content, str) else _text_of(content)
    return str(message)


def _meaningful(text: str) -> bool:
    """False for empty / emoji-only / no-word input (nothing to extract)."""
    return bool(re.search(r"[\u4e00-\u9fff]|[A-Za-z0-9]", text or ""))


def _segment_for(clause: str, start: int, end: int) -> str:
    """Smallest comma-segment of ``clause`` covering [start, end), with subject
    back-fill when the segment is a short verb tail."""
    left, right = 0, len(clause)
    for m in _SEGMENT_SPLIT_RE.finditer(clause):
        if m.start() <= start:
            left = m.end()
        if m.end() >= end and right == len(clause):
            right = m.start()
            break
    seg_start, seg_end = left, right if right > start else len(clause)
    # first splitter AFTER the match closes the segment
    for m in _SEGMENT_SPLIT_RE.finditer(clause):
        if m.start() >= end:
            seg_end = m.start()
            break
    segment = clause[seg_start:seg_end].strip()
    if len(segment) <= 6 and seg_start > 0:
        prev_split = max(
            (m.end() for m in _SEGMENT_SPLIT_RE.finditer(clause[:seg_start])),
            default=0,
        )
        segment = clause[prev_split:seg_end].strip()
    return segment[:_MAX_CONSTRAINT_CHARS]


def _norm(text: str) -> str:
    return re.sub(r"[\s，,。；;！!？?\n、]", "", text)


def extract_constraints(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Cue-driven constraint + unknown extraction over clause segments."""
    constraints: List[Dict[str, str]] = []
    seen: set = set()
    for clause in (c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "")):
        if not clause:
            continue
        for cue_re, kind in _CUES:
            for match in cue_re.finditer(clause):
                segment = _segment_for(clause, match.start(), match.end())
                if len(_norm(segment)) < 2:
                    continue
                key = (kind, _norm(segment))
                if key in seen or _norm(segment) in {k[1] for k in seen}:
                    continue
                seen.add(key)
                constraints.append({"kind": kind, "text": segment})
    constraints.sort(key=lambda c: _KIND_ORDER.get(c["kind"], 99))
    constraints = constraints[:_MAX_CONSTRAINTS]

    unknowns: List[str] = []
    for clause in (c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "")):
        if len(unknowns) >= _MAX_UNKNOWN:
            break
        if _QUESTION_RE.search(clause) and clause and clause not in unknowns:
            unknowns.append(clause[:80])
    return constraints, unknowns


def _intent_line(text: str) -> str:
    """One-line intent: prefer the first action clause; a short question
    prefix before '：' yields to what follows it."""
    body = (text or "").strip()
    if "：" in body[:14]:
        body = body.split("：", 1)[1].strip() or body
    elif ":" in body[:14]:
        body = body.split(":", 1)[1].strip() or body
    best = ""
    for sentence in _SENT_SPLIT_RE.split(body):
        s = sentence.strip()
        if len(s) < 3:
            continue
        if not best:
            best = s
        if _VERB_RE.search(s):
            return s[:_MAX_INTENT_CHARS]
    return best[:_MAX_INTENT_CHARS]


def _ambiguity_score(current: str, unknowns: List[str], recent: List[str]) -> float:
    score = 0.2
    if "?" in current or "？" in current:
        score += 0.3
    elif unknowns:
        score += 0.2
    if _PRIOR_REF_RE.search(current) and not recent:
        score += 0.2
    return min(1.0, round(score, 2))


def build_card_rules(current: str, recent: Optional[List[str]] = None) -> Dict[str, Any]:
    """Rules card {intent, constraints[], unknowns[], ambiguity_score}."""
    constraints, unknowns = extract_constraints(current)
    return {
        "intent": _intent_line(current),
        "constraints": constraints,
        "unknowns": unknowns,
        "ambiguity_score": _ambiguity_score(current, unknowns, list(recent or [])),
    }


# ── optional aux-model variant (agent.turn_intent_llm) ──────────────────

_LLM_SYSTEM = (
    "Extract the user's intent for THIS turn. Return STRICT JSON only, no prose: "
    '{"intent": str (one line), "constraints": [{"kind": one of '
    '"must-not|must|scope|ordering|format|direct|priority", "text": str}], '
    '"unknowns": [str], "ambiguity_score": 0.0-1.0}. Constraints are only the '
    "user's EXPLICIT asks (prohibitions, musts, scope limits, ordering, format, "
    "do-not-ask). Quote the user's own wording. Keep total under 400 chars."
)


def build_card_llm(current: str, recent: Optional[List[str]] = None) -> Dict[str, Any]:
    """One auxiliary call; any failure falls back to the rules card."""
    base = build_card_rules(current, recent)
    try:
        from agent.auxiliary_client import call_llm

        history = "\n".join(f"- {t}" for t in (recent or [])[:_RECENT_USER_TURNS])
        user_payload = (f"Recent user turns (context only):\n{history}\n\n" if history else "")
        user_payload += f"Current user message:\n{current[:6000]}"
        response = call_llm(
            task="turn_intent",
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_payload},
            ],
            max_tokens=_LLM_MAX_TOKENS,
            temperature=0.0,
            timeout=_LLM_TIMEOUT_SECONDS,
        )
        content = ""
        choices = getattr(response, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            content = (getattr(msg, "content", None) or "").strip()
        match = re.search(r"\{.*\}", content or "", re.DOTALL)
        card = json.loads(match.group(0)) if match else None
        if not isinstance(card, dict) or not isinstance(card.get("intent"), str):
            raise ValueError("malformed intent card")
        valid_kinds = set(_KIND_LABELS)
        raw_constraints = [
            {"kind": c.get("kind") if c.get("kind") in valid_kinds else "must",
             "text": str(c.get("text") or "")[:_MAX_CONSTRAINT_CHARS]}
            for c in (card.get("constraints") or []) if isinstance(c, dict) and c.get("text")
        ][:_MAX_CONSTRAINTS]
        try:
            ambiguity = max(0.0, min(1.0, float(card.get("ambiguity_score", 0.2))))
        except (TypeError, ValueError):
            ambiguity = 0.2
        return {
            "intent": str(card["intent"])[:_MAX_INTENT_CHARS],
            "constraints": raw_constraints,
            "unknowns": [str(u)[:80] for u in (card.get("unknowns") or [])[:_MAX_UNKNOWN]],
            "ambiguity_score": ambiguity,
            "llm": True,
        }
    except Exception as exc:  # fail-open: the turn must never break
        logger.debug("turn_intent llm variant failed, using rules card: %s", exc)
        base["llm_fallback"] = True
        return base


# ── flags (fail-closed, default OFF; task_checkpoint.enabled pattern) ────

def _flags() -> Tuple[bool, bool]:
    try:
        from hermes_cli.config import load_config_readonly
        section = load_config_readonly().get("agent") or {}
        if not isinstance(section, dict):
            return False, False
        return section.get("turn_intent") is True, section.get("turn_intent_llm") is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False, False


def render_block(card: Dict[str, Any]) -> str:
    """Compact sidecar block, hard-capped at ~400 chars."""
    lines = [BLOCK_HEADER, f"意图: {card.get('intent') or ''}"]
    constraints = card.get("constraints") or []
    if constraints:
        lines.append("约束:")
        lines.extend(f"- {_KIND_LABELS.get(c['kind'], c['kind'])}: {c['text']}" for c in constraints)
    unknowns = card.get("unknowns") or []
    if unknowns:
        lines.append("未知项（作为下一步取证，不要空答）:")
        lines.extend(f"- 待确认: {u}" for u in unknowns)
    score = float(card.get("ambiguity_score") or 0.2)
    lines.append(f"歧义: {'高' if score >= 0.7 else '中' if score >= 0.4 else '低'}")
    block = "\n".join(lines)
    if len(block) <= _MAX_BLOCK_CHARS:
        return block
    # Over budget: drop unknowns, then constraints from the tail.
    lines = [l for l in lines if not l.startswith("- 待确认")]
    block = "\n".join(lines)
    while len(block) > _MAX_BLOCK_CHARS and len(constraints) > 1:
        constraints.pop()
        rebuilt = [BLOCK_HEADER, f"意图: {card.get('intent') or ''}", "约束:"]
        rebuilt.extend(f"- {_KIND_LABELS.get(c['kind'], c['kind'])}: {c['text']}" for c in constraints)
        idx = next(i for i, l in enumerate(lines) if l.startswith("歧义:"))
        rebuilt.append(lines[idx])
        lines, block = rebuilt, "\n".join(rebuilt)
    return block[:_MAX_BLOCK_CHARS]


def _recent_user_texts(messages: List[Any], current_turn_user_idx: int) -> List[str]:
    """Up to _RECENT_USER_TURNS prior user texts, chronological order."""
    out: List[str] = []
    for row in reversed(list(messages[:max(0, current_turn_user_idx)])):
        if len(out) >= _RECENT_USER_TURNS:
            break
        if isinstance(row, dict) and row.get("role") == "user":
            text = _text_of(row)
            if _meaningful(text):
                out.append(text)
    out.reverse()
    return out


def maybe_turn_intent_block(
    original_user_message: Any, messages: List[Any], current_turn_user_idx: int,
) -> str:
    """'' when the feature is off or nothing meaningful extracts (flag-off
    calls build nothing, so injected bytes stay identical)."""
    rules_on, llm_on = _flags()
    if not (rules_on or llm_on):
        return ""
    current = _text_of(original_user_message)
    if not _meaningful(current) or current.strip().startswith("/"):
        return ""
    recent = _recent_user_texts(messages, current_turn_user_idx)
    started = time.perf_counter()
    try:
        card = build_card_llm(current, recent) if llm_on else build_card_rules(current, recent)
    except Exception:
        logger.debug("turn intent extraction failed; skipping block", exc_info=True)
        return ""
    card["extract_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return render_block(card)
