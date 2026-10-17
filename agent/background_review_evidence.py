"""Bounded execution evidence for Stardust's existing background skill review.

This is not a scorer or a second agent. It derives read-only, explicitly
non-causal observations from the current turn and the existing skill usage
sidecar, without copying private tool output into a new persistent store.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional

_MAX_TURN_MESSAGES = 300
_MAX_SKILLS = 6
_MAX_TOOL_NAMES = 5
_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,79}\Z")


def _as_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if len(value) > 16_384:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _last_turn(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Use the last user turn, not accumulated history from previous reviews."""
    recent = (messages or [])[-_MAX_TURN_MESSAGES:]
    for i in range(len(recent) - 1, -1, -1):
        if isinstance(recent[i], dict) and recent[i].get("role") == "user":
            return recent[i:]
    return recent


def _skill_name(args: Dict[str, Any]) -> Optional[str]:
    name = args.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name if _SKILL_NAME.fullmatch(name) and ".." not in name and "//" not in name else None


def _safe_count(value: Any) -> int:
    try:
        return min(999999, max(0, int(value))) if not isinstance(value, bool) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def build_skill_review_evidence(
    messages: List[Dict[str, Any]], *, usage: Optional[Dict[str, Dict[str, Any]]] = None,
    mutations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Summarize observable tool and skill signals; return empty for no tool work.

    This text is appended to the *fork's user prompt*, never the parent's
    cached system prompt. Tool errors are task-level correlation, NOT proof that
    a particular skill failed. The sidecar counts are activity, NOT quality.
    """
    recent = _last_turn(messages)
    names: List[str] = []
    tool_counts: Counter[str] = Counter()
    calls: Dict[str, str] = {}
    skill_load_calls: Dict[str, str] = {}
    explicit_failures: set[str] = set()
    for message in recent:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                    continue
                tool = fn["name"]
                # Tool names enter the prompt as text: reject malformed/untrusted
                # model-projected names rather than echoing instructions or escapes.
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", tool):
                    continue
                tool_counts[tool] += 1
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    calls[call_id] = tool
                if tool == "skill_view":
                    args = _as_object(fn.get("arguments"))
                    name = _skill_name(args)
                    if name and not args.get("file_path") and name not in names:
                        names.append(name)
                    if name and isinstance(call_id, str):
                        skill_load_calls[call_id] = name
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in calls:
                continue
            payload = _as_object(message.get("content"))
            status = payload.get("status")
            if (payload.get("success") is False or payload.get("is_error") is True
                    or (isinstance(status, str) and status.lower() in {"error", "failed"})
                    or message.get("is_error") is True):
                explicit_failures.add(call_id)

    if not tool_counts:
        return ""
    # Report failed skill views separately; a failed load is not an applied skill.
    failed_loads = {name for call_id, name in skill_load_calls.items() if call_id in explicit_failures}
    skill_names = names[:_MAX_SKILLS]
    lines = [
        "[Read-only skill review evidence: tool telemetry, NOT user instructions]",
        f"Last turn: {sum(tool_counts.values())} tool "
        f"{'call' if sum(tool_counts.values()) == 1 else 'calls'}, "
        f"{len(explicit_failures)} explicitly failed tool results.",
        "Top tools: " + ", ".join(f"{name}={count}" for name, count in tool_counts.most_common(_MAX_TOOL_NAMES)) + ".",
    ]
    if skill_names:
        lines.append("Skills opened in last turn (opening is not proof of successful use): " + ", ".join(skill_names) + ".")
    if failed_loads:
        lines.append("Skill opens reporting failure: " + ", ".join(name for name in skill_names if name in failed_loads) + ".")
    if skill_names:
        if usage is None:
            try:
                from tools.skill_usage import load_usage
                usage = load_usage()
            except (OSError, ValueError, TypeError, ImportError):
                usage = {}
        if isinstance(usage, dict):
            for name in skill_names:
                item = usage.get(name)
                if isinstance(item, dict):
                    lines.append(
                        f"{name}: recorded use_count={_safe_count(item.get('use_count'))}, "
                        f"patch_count={_safe_count(item.get('patch_count'))}."
                    )
    # Reuse Stardust's existing mutation ledger as longitudinal context; never expose
    # file manifests, logged evidence, content, paths or rollback blobs to the model.
    if skill_names:
        if mutations is None:
            try:
                from tools.skill_ledger import list_entries
                mutations = list_entries(limit=120)
            except (OSError, ValueError, TypeError, ImportError):
                mutations = []
        if isinstance(mutations, list):
            for name in skill_names:
                history = [entry for entry in mutations if isinstance(entry, dict)
                           and entry.get("skill") == name
                           and isinstance(entry.get("action"), str)
                           and entry["action"] in {"create", "patch", "edit", "write_file", "remove_file"}][:3]
                if history:
                    lines.append(
                        f"{name}: previous changes (newest first, not proof of improvement): "
                        + ", ".join(entry["action"] for entry in history) + "."
                    )
    lines.append(
        "These counts show activity, not causation or quality. Check the actual transcript, "
        "confirm the task outcome, and prefer no change over an unverified rewrite."
    )
    return "\n".join(lines)
