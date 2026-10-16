"""Conversation self-distillation cues for the existing background skill review.

This file provides read-only, bounded evidence. The LLM still evaluates the
conversation and skill_manage/Curator still own every persisted mutation.
No transcript text or private tool payload is copied to a separate store.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

_MAX_MESSAGES = 240
_MAX_MARKERS = 8
_MAX_TOOL_NAMES = 5

# Deliberately strong, initial-position signals; a passing mention of "wrong"
# inside a task should not enqueue a costly review fork.
_CORRECTION = re.compile(
    r"^(?:不对(?:[，,。！!：:]|$)|不是(?:[，,。！!：:]|这个意思|这样|我要|我说的)|"
    r"你(?:理解错了|搞错了|回答错了|说错了|没有理解|没理解|的(?:回答|方案|理解)(?:我)?(?:不满意|不对|不符合))|"
    r"(?:这|你)(?:不符合|不正确|不满意)|重新理解|理解有误|"
    r"错了(?:[，,。！!]|$)|"
    r"no(?:[!,.:]|$)|that's wrong\b|that is wrong\b|"
    r"you (?:misunderstood|got it wrong)\b|"
    r"not what i (?:meant|asked|wanted)\b|"
    r"you didn't (?:understand|follow)\b)",
    re.IGNORECASE,
)
_CONFIRMATION = re.compile(
    r"^(?:对(?:[，,。！!]|$|，就是|就是)|是的[，,。！! ]|"
    r"(?:这次|现在)(?:就)?对了|就是这样|这样才对|问题解决了|"
    r"(?:已经|现在)成功了|"
    r"(?:yes[,!:. ]|that's (?:it|right)\b|"
    r"(?:it|that) (?:works|worked)\b|correct[,!:. ]))",
    re.IGNORECASE,
)
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content[:220].strip()
    if isinstance(content, list):
        for part in content[:4]:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"][:220].strip()
    return ""


def _recent(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, (list, tuple)):
        return []
    return [m if isinstance(m, dict) else {} for m in messages[-_MAX_MESSAGES:]]


def _feedback_events(recent: list[dict[str, Any]]) -> list[tuple[int, str]]:
    events: list[tuple[int, str]] = []
    has_assistant_reply = False
    for pos, message in enumerate(recent):
        role = message.get("role")
        if role == "assistant" and _message_text(message):
            has_assistant_reply = True
        elif role == "user":
            if has_assistant_reply:
                text = _message_text(message)
                if _CORRECTION.match(text):
                    events.append((pos, "correction"))
                elif _CONFIRMATION.match(text):
                    events.append((pos, "confirmation"))
            # The next user turn should only be compared to an intervening reply.
            has_assistant_reply = False
    return events


def has_actionable_self_distillation_feedback(messages: Any) -> bool:
    """Trigger on a new explicit correction, or confirmation after recent correction.

    Plain praise alone is not worth another LLM call. This is a CPU-only
    heuristic, not a claim that the agent was wrong or the task was successful.
    """
    recent = _recent(messages)
    if not recent:
        return False
    last_user = next((i for i in range(len(recent) - 1, -1, -1)
                      if recent[i].get("role") == "user"), -1)
    if last_user < 0:
        return False
    events = _feedback_events(recent)
    current = next((kind for i, kind in reversed(events) if i == last_user), None)
    if current == "correction":
        return True
    return current == "confirmation" and any(
        kind == "correction" and 0 < last_user - i <= 24 for i, kind in events
    )


def _tool_verdicts(recent: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    calls: dict[str, str] = {}
    successes: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    for message in recent:
        if message.get("role") == "assistant":
            raw_calls = message.get("tool_calls")
            for tc in (raw_calls if isinstance(raw_calls, (list, tuple)) else [])[:64]:
                if not isinstance(tc, dict):
                    continue
                function = tc.get("function")
                if not isinstance(function, dict):
                    continue
                name, call_id = function.get("name"), tc.get("id")
                if isinstance(name, str) and _TOOL_NAME.fullmatch(name) and isinstance(call_id, str) and len(call_id) <= 128:
                    calls[call_id] = name
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in calls:
                continue
            raw = message.get("content")
            if isinstance(raw, str) and len(raw) <= 16_384:
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = None
            name = calls.pop(call_id)
            if not isinstance(raw, dict):
                continue
            status = raw.get("status")
            if (raw.get("success") is False or raw.get("is_error") is True
                    or message.get("is_error") is True
                    or isinstance(status, str) and status.lower() in {"failed", "error"}):
                failures[name] += 1
            elif (raw.get("success") is True or isinstance(status, str)
                  and status.lower() in {"success", "completed", "ok"}):
                successes[name] += 1
    touched = (failures + successes).most_common(_MAX_TOOL_NAMES)
    summary = [f"{name}: {successes[name]} reported successes, {failures[name]} reported failures"
               for name, _ in touched]
    return sum(successes.values()), sum(failures.values()), summary


def build_conversation_distillation_brief(messages: Any) -> str:
    """Offer bounded, non-identifying feedback/outcome markers for the review fork."""
    recent = _recent(messages)
    events = _feedback_events(recent)
    ok, bad, tools = _tool_verdicts(recent)
    if not events and not ok and not bad:
        return ""
    lines = [
        "[Self-distillation signals from this agent's OWN conversation; read-only evidence]",
        "Compare the original user request, the assistant's earlier decisions, corrections, "
        "revisions, and confirmed outcomes across the conversation, not only the last turn.",
    ]
    if events:
        counts = Counter(kind for _, kind in events)
        lines.append(
            f"Observed feedback: {counts['correction']} explicit user corrections and "
            f"{counts['confirmation']} user confirmations (neither is a standalone task-quality score)."
        )
        sequence = [f"{kind} ({len(recent) - pos} messages before snapshot end)"
                    for pos, kind in events[-_MAX_MARKERS:]]
        lines.append("Recent sequence: " + "; ".join(sequence) + ".")
    if ok or bad:
        lines.append(f"Tool-result envelopes reported {ok} successes and {bad} failures; "
                     "a tool's success flag is NOT verification of the user's task outcome.")
        lines.extend(tools)
    lines.extend([
        "Distill what worked AND what did not, but only into reusable steps, decision rules, "
        "verification conditions and pitfalls that the transcript actually supports.",
        "Treat unverified fixes and unresolved errors as hypotheses, not established guidance. "
        "Do not copy personal text, secrets or full transcripts into Skills. "
        "Read the target Skill and avoid duplicating an existing lesson. "
        "No justified improvement means no write.",
    ])
    return "\n".join(lines)
