"""Shared helpers for classifying tool result payloads."""

from __future__ import annotations

import json
from typing import Any, Optional


FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})


# Tools whose interrupted/dangling execution is safe to discard because they
# cannot mutate either external state or Hermes session state. Unknown/plugin/
# MCP tools stay effect-capable by default.
NO_EFFECT_TOOL_NAMES = frozenset({
    "read_file", "search_files", "session_search", "skill_view", "skills_list",
    "web_extract", "web_search", "vision_analyze", "browser_snapshot",
    "browser_get_images", "browser_console", "read_terminal",
})


def tool_may_have_side_effect(tool_name: str) -> bool:
    return tool_name not in NO_EFFECT_TOOL_NAMES


def declared_tool_verdict(data: Any) -> Optional[bool]:
    """A result's own outcome flag: ``success``/``ok`` when set to a real bool
    (``False`` if either says so), else None.

    A declared verdict outranks text sniffing: success payloads legitimately
    name ``failed``/``error`` — a cron listing can show a job whose last run
    errored without the listing itself failing.
    """
    if not isinstance(data, dict):
        return None
    flags = [data[key] for key in ("success", "ok") if isinstance(data.get(key), bool)]
    return all(flags) if flags else None


def tool_result_outcome(tool_name: str, data: Any) -> str:
    """Classify declared envelope facts, never output text or task completion.

    Non-final/unknown states cannot be upgraded by an optimistic success flag.
    Terminal exit codes are command outcomes, including timeout's conventional 124.
    """
    if not isinstance(data, dict):
        return "unknown"
    status = data.get("status")
    status = status.strip().lower() if isinstance(status, str) else ""
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"timeout", "timed_out"}:
        return "timed_out"
    if data.get("is_error") is True or status in {"failed", "error", "blocked"}:
        return "failed"
    if status == "unknown":
        return "unknown"
    if status in {
        "pending", "running", "queued", "in_progress",
        "started", "background", "yielded_to_background",
    }:
        return "pending"
    verdict = declared_tool_verdict(data)
    if verdict is False:
        return "failed"
    if tool_name == "terminal":
        exit_code = data.get("exit_code")
        if type(exit_code) is int:
            if exit_code == 124:
                return "timed_out"
            return "success" if exit_code == 0 else "failed"
        # A present but missing/invalid exit code is not a completed command.
        if "exit_code" in data:
            return "unknown"
    if verdict is True or status in {"success", "completed", "ok"}:
        return "success"
    return "unknown"


def file_mutation_result_landed(tool_name: str, result: Any) -> bool:
    """Return True when a file mutation result proves the write landed."""
    if tool_name not in FILE_MUTATING_TOOL_NAMES or not isinstance(result, str):
        return False
    try:
        data = json.loads(result.strip())
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("error"):
        return False
    if tool_name == "write_file":
        return "bytes_written" in data
    if tool_name == "patch":
        return data.get("success") is True
    return False
