"""Structured-output schema helpers for delegate_task.

Optional per-task ``output_schema`` (a JSON Schema object): the child gets an
OUTPUT CONTRACT block appended to its context, the parent validates the final
answer with jsonschema, and on failure sends exactly ONE bounded retry turn
carrying the validation errors verbatim (more retries make frontier models
drop fields that were right the first time; the schema is never re-pasted).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def coerce_output_schema(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``(schema, None)`` when usable, ``(None, error)`` when not; ``None`` input
    passes through as ``(None, None)`` (no schema requested)."""
    if raw is None:
        return None, None
    if isinstance(raw, str):
        # Models sometimes double-encode the schema as a JSON string.
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None, "output_schema must be a JSON Schema object, got a non-JSON string."
        if not isinstance(raw, dict):
            return None, "output_schema must be a JSON Schema object."
    if not isinstance(raw, dict):
        return None, f"output_schema must be a JSON Schema object, got {type(raw).__name__}."
    try:
        from jsonschema.validators import validator_for  # type: ignore[import-untyped]
        validator_for(raw).check_schema(raw)
    except ImportError:
        # Degrade to accepting the dict as-is so delegation still works without jsonschema.
        logger.debug("jsonschema unavailable; skipping output_schema meta-validation")
    except Exception as exc:
        return None, f"output_schema is not a valid JSON Schema: {exc}"
    return raw, None


def append_output_contract(context: Optional[str], schema: Dict[str, Any]) -> str:
    """Append the explicit output contract block to a child's context."""
    try:
        schema_text = json.dumps(schema, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        schema_text = str(schema)
    block = ("OUTPUT CONTRACT (machine-validated):\n"
             "Your FINAL response must be ONLY the JSON value that validates against this JSON "
             "Schema — no prose before or after it, no code fence, no explanation. Anything else "
             "costs a correction turn and, if it fails again, is handed to the caller unvalidated.\n"
             f"{schema_text}")
    base = (context or "").rstrip()
    return f"{base}\n\n{block}" if base else block


def _candidate_source(text: str) -> str:
    """Strip a surrounding markdown JSON fence while retaining prose for candidate scanning."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[: -3]
        raw = raw.strip()
        if raw.lower().startswith("json\n"):
            raw = raw.split("\n", 1)[1]
    return raw


def _json_candidate_end(raw: str, start: int) -> Optional[int]:
    """Return the end of a balanced object/array span, or ``None`` if unbalanced."""
    pairs = {"}": "{", "]": "["}
    stack = [raw[start]]
    in_string = False
    escaped = False

    for index in range(start + 1, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack[-1] != pairs[char]:
                return None
            stack.pop()
            if not stack:
                return index + 1
    return None


def _json_candidates(raw: str) -> List[Tuple[str, Any]]:
    """Return complete object/array candidates in text order, skipping nested spans once parsed."""
    decoder = json.JSONDecoder()
    candidates: List[Tuple[str, Any]] = []
    cursor = 0
    while cursor < len(raw):
        object_start = raw.find("{", cursor)
        array_start = raw.find("[", cursor)
        starts = [start for start in (object_start, array_start) if start >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            parsed, end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            # Do not promote a valid-looking nested value from inside a malformed
            # outer object/array. It is not an independent final JSON candidate.
            end = _json_candidate_end(raw, start)
            if end is None:
                break
            cursor = end
            continue
        candidates.append((raw[start:end], parsed))
        cursor = end
    return candidates


def extract_json_candidate(text: str) -> str:
    """Strip prose/fences and return the last complete object or array candidate."""
    raw = _candidate_source(text)
    candidates = _json_candidates(raw)
    if candidates:
        return candidates[-1][0]

    # Preserve a useful malformed span for the caller's JSON parse diagnostic.
    spans = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start >= 0 and end > start:
            spans.append((start, raw[start : end + 1]))
    for _start, candidate in sorted(spans):
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            continue
    return spans[0][1] if spans else raw


def validate_output(text: str, schema: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """``(True, [])`` or ``(False, errors)`` with strings suitable for the retry turn."""
    raw = _candidate_source(text or "")
    candidates = _json_candidates(raw)
    if not candidates:
        candidate = extract_json_candidate(raw)
        if not candidate.strip():
            return False, ["Response was empty — expected a JSON value matching the schema."]
        try:
            json.loads(candidate)
        except (ValueError, TypeError) as exc:
            return False, [f"Response is not valid JSON: {exc}"]
        candidates = [(candidate, json.loads(candidate))]
    try:
        from jsonschema.validators import validator_for  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("jsonschema unavailable; accepting parsed JSON without validation")
        return True, []

    validator = validator_for(schema)(schema)
    # The last complete JSON value is the child's final candidate. Earlier values
    # may be examples, so they must not override a malformed final answer.
    _candidate, parsed = candidates[-1]
    errors = sorted(validator.iter_errors(parsed), key=lambda e: list(e.absolute_path))
    rendered = [  # bound error volume for the retry prompt
        "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path) + f": {err.message}"
        for err in errors[:10]]
    return not rendered, rendered


def build_retry_message(errors: List[str]) -> str:
    """Single bounded retry turn: errors verbatim, schema deliberately NOT re-pasted."""
    error_block = "\n".join(f"- {e}" for e in errors)
    return ("Your previous final response was rejected by the output contract "
            "validator. Validation errors:\n" f"{error_block}\n\n"
            "Reply with ONLY the corrected JSON object matching the OUTPUT "
            "CONTRACT schema from your task context. No prose, no explanations.")
