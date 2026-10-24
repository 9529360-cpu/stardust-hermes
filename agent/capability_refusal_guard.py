"""Capability-refusal guard (F5.4 / D5-03): one-shot recheck when the model
claims it cannot do something a currently-available tool can do.

Detects generic inability claims ("我无法访问本地文件" / "I cannot access local
files") in a final text answer and, when the user's message was an execution
request, a matching tool is available, and that tool was NOT tried this turn,
allows at most ONE corrective continuation naming the tool. Real failures
(the tool WAS tried and errored) never trigger: those answers must cite the
actual error. Pure capability questions ("can you read files?") never trigger.
Opt-in via ``agent.capability_refusal_guard`` (default off); flag off keeps
``finish_text_response`` byte-identical.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# The guard may add at most ONE corrective continuation per turn: after one
# nudge the model's next answer is final (spec: "at most ONE").
_MAX_GUARD_CONTINUATIONS = 1

# Generic inability phrasings (zh + en). These claim a MISSING capability, not
# a concrete failed attempt; specific failure mentions never match.
_INABILITY_ZH_RE = re.compile(
    r"无法(?:访问|读取|打开|查看|执行|运行|连接)"
    r"|不能(?:访问|读取|打开|查看|执行|运行|连接)"
    r"|没有[^，。;；!！?？\n]{0,20}权限"
    r"|访问不了|读不了|打不开"
)
_INABILITY_EN_RE = re.compile(
    r"\b(?:i\s+(?:can'?[\u2019']?t|cannot|am\s+unable\s+to|do(?:n'?[\u2019']?t|es\s+not)\s+have\s+access\s+to)"
    r"|unable\s+to)\b[^.!?]{0,120}?"
    r"\b(?:access|read|open|view|reach|connect(?:\s+to)?|files?|documents?)\b",
    re.IGNORECASE,
)
# An answer that already names a concrete failure is a real attempt report,
# never a generic refusal (group B/C protection).
_REAL_FAILURE_RE = re.compile(
    r"失败|错误|报错|超时|不存在|未找到|无(?:法)?(?:权限|网络)"
    r"|\b(?:failed|failure|error|timed?\s*out|not\s+found|denied|refused"
    r"|unreachable|doesn'?t\s+exist|does\s+not\s+exist)\b",
    re.IGNORECASE,
)
# Pure capability question (group E protection) — no execution ask.
_CAPABILITY_QUESTION_RE = re.compile(
    r"能(?:不能|否|可以)?(?:访问|读取|打开|查看|执行|运行)"
    r"|可不可以|有没有(?:权限|工具)"
    r"|\b(?:can|could)\s+you\b[^.!?]{0,80}?\b(?:access|read|open|view|files?|documents?)\b"
    r"|\bdo\s+you\s+have\s+(?:access|the\s+ability)\b",
    re.IGNORECASE,
)
# Execution request for file-ish content: an imperative asking the model to
# read/open/summarize/extract from a named local path (not a question).
_EXECUTION_REQUEST_RE = re.compile(
    r"(?:阅读|读一?下|读取|打开|看看|查看|翻[一阅]|总结|摘要|提炼|提取|找(?:出)?|检查|分析)"
    r"|\b(?:read|open|summar\w*|extract|check|analyz\w*|look(?:ing)?\s+at|take\s+a\s+look)\b",
    re.IGNORECASE,
)

# Inability domain -> tools that provide it. Matched against the CURRENT
# turn's available tools (``agent.valid_tool_names``), so the guard never
# fires when the capability is genuinely absent (group D protection).
_DOMAIN_TOOLS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("local_files", ("read_file", "search_files")),
)


def capability_refusal_guard_enabled(agent) -> bool:
    """Read the opt-in ``agent.capability_refusal_guard`` flag once per agent,
    using the neighbouring config-read pattern (``commitment_stall_zh_enabled``)."""
    cached = getattr(agent, "_capability_refusal_guard_cache", None)
    if cached is None:
        try:
            from hermes_cli.config import load_config

            section = (load_config() or {}).get("agent", {})
            cached = (
                isinstance(section, dict)
                and section.get("capability_refusal_guard") is True
            )
        except Exception:
            cached = False
        agent._capability_refusal_guard_cache = cached
    return cached


def _available_tools(agent) -> set:
    names = getattr(agent, "valid_tool_names", None) or []
    try:
        return {n for n in names if isinstance(n, str)}
    except TypeError:
        return set()


def _turn_start(messages: List[Dict[str, Any]], user_message: Any) -> int:
    """Index of the last original user row starting this turn (retry nudges
    appended after it are not a new request). Mirrors ``pure_commitment_stall``."""
    return next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") == "user"
            and messages[i].get("content") == user_message
        ),
        0,
    )


def _claims_local_file_inability(text: str) -> bool:
    return bool(_INABILITY_ZH_RE.search(text) or _INABILITY_EN_RE.search(text))


def should_nudge_capability_refusal(
    agent,
    user_message: Any,
    final_response: str,
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """Return the tool name when a false-inability final answer should get ONE
    corrective nudge, else ``None``.

    Fires only when ALL hold: flag on; the user message is an execution
    request (not a capability question); the answer contains a generic
    inability claim about a guardable domain; a tool for that domain is
    available this turn; no tool was tried this turn (a real failure answer
    must survive and cite its error); the answer reports no real failure; the
    per-turn guard budget (1) remains.
    """
    if not capability_refusal_guard_enabled(agent):
        return None
    if (
        getattr(agent, "_capability_refusal_continuations", 0)
        >= _MAX_GUARD_CONTINUATIONS
    ):
        return None
    from agent.codex_responses_adapter import _summarize_user_message_for_log

    request = _summarize_user_message_for_log(user_message).strip()
    if not request or request.endswith(("?", "？")):
        return None
    if _CAPABILITY_QUESTION_RE.search(request):
        return None
    if not _EXECUTION_REQUEST_RE.search(request):
        return None
    start = _turn_start(messages, user_message)
    if any(m.get("role") == "tool" or m.get("tool_calls") for m in messages[start:]):
        # A tool was tried this turn: any inability wording that follows a real
        # attempt reports that attempt's outcome — never overridden.
        return None
    text = (agent._strip_think_blocks(final_response or "") or "").strip()
    if not text or len(text) > 600 or _REAL_FAILURE_RE.search(text):
        return None
    if not _claims_local_file_inability(text):
        return None
    for _domain, tools in _DOMAIN_TOOLS:
        for tool in tools:
            if tool in _available_tools(agent):
                return tool
    return None


def build_capability_refusal_nudge(tool_name: str, user_message: Any) -> str:
    """The ONE corrective continuation, naming the available tool. It instructs
    the model to use the tool or, after a genuine attempt fails, report the
    actual error — never to touch paths the user did not name."""
    from agent.codex_responses_adapter import _summarize_user_message_for_log

    request = _summarize_user_message_for_log(user_message).strip()
    return (
        f"[capability note] The {tool_name} tool IS available to you in this "
        f"session. Do not claim you cannot access what it can read. Either use "
        f"{tool_name} now to fulfil the request, or — only after a genuine "
        f"attempt fails — reply citing the exact error the tool returned. "
        f"Do not access any path other than the one the user named. "
        f"(user request: {request[:200]})"
    )
