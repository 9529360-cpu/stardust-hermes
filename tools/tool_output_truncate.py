"""Head/tail truncation for oversized tool output (terminal, execute_code, MCP results).

One algorithm and one notice wording: 40% head (errors surface early) / 60% tail (the most
recent lines matter most) around a single ``... [<LABEL> TRUNCATED - N <unit> omitted out of
T total] ...`` marker, so downstream code that recognises the marker sees one shape. Limits
live in ``tools/tool_output_limits.py``; line-snapped, path-bearing footers (web/browser,
read_file pagination) are different products and stay separate.
"""

from __future__ import annotations

import io
import re

HEAD_RATIO = 0.4

# Advisory text matches, never a tool success/failure classifier.
_KEY_LINE = re.compile(
    r"\b(?:traceback|[\w.]{0,80}error|exception|failed|failure|fatal|panic|"
    r"warning|warn|timed?\s*out|exit[ _-]*(?:code|status))\b", re.IGNORECASE,
)


def saved_output_preview(text: str, max_chars: int, *, fallback: str | None = None,
                         byte_budget: bool = False, tail_source: str | None = None) -> str:
    """Index diagnostic lines from already-saved text inside the existing preview cap.

    Keep at most twelve candidates (first six and last six), each clipped to 200
    characters. Scan lines without materializing a second full list. Line numbers
    refer to the saved text, not the head/tail window. No matches leaves the old
    caller preview untouched. Call only after a successful write/redaction.
    """
    def size(value):
        return len(value.encode("utf-8", errors="replace")) if byte_budget else len(value)

    def clip(value, budget):
        if byte_budget:
            return value.encode("utf-8", errors="replace")[:max(0, budget)].decode("utf-8", errors="ignore")
        return value[:max(0, budget)]

    candidates = []
    for number, line in enumerate(io.StringIO(text, newline=None), 1):
        if _KEY_LINE.search(line):
            item = f"L{number}: {line.rstrip()[:200]}\n"
            if len(candidates) == 12:
                del candidates[6]
            candidates.append(item)
    if not candidates or max_chars < 256:
        return fallback if fallback is not None else truncate_head_tail(text, max_chars)
    summary = "Key lines (advisory; saved-file line numbers):\n"
    summary_cap = min(1500, max_chars // 2)
    for item in candidates:
        if size(summary + item) > summary_cap:
            break
        summary += item
    source = text if tail_source is None else re.sub(
        r"\n\n\.\.\. \[[A-Z ]+ TRUNCATED - [^\n]+\] \.\.\.\n\n", "\n", tail_source,
    )
    marker = "\n... [OUTPUT TRUNCATED; see saved file] ...\n"
    available = max_chars - size(summary + marker)
    head, tail = head_tail_split(max(0, available))
    end = (source.encode("utf-8", errors="replace")[-tail:].decode("utf-8", errors="ignore")
           if byte_budget and tail else source[-tail:] if tail else "")
    return clip(source, head) + marker + summary + end


def truncation_notice(omitted: int, total: int, *, label: str = "OUTPUT", unit: str = "chars") -> str:
    return f"\n\n... [{label} TRUNCATED - {omitted:,} {unit} omitted out of {total:,} total] ...\n\n"


def head_tail_split(budget: int) -> tuple[int, int]:
    """``(head, tail)`` character budgets for ``budget`` total."""
    head = int(budget * HEAD_RATIO)
    return head, budget - head


def truncate_head_tail(text: str, max_chars: int, *, label: str = "OUTPUT") -> str:
    """``text`` unchanged when it fits ``max_chars``; otherwise head + notice + tail (the kept
    text is exactly ``max_chars`` long, the notice rides on top)."""
    if len(text) <= max_chars:
        return text
    head, tail = head_tail_split(max_chars)
    omitted = len(text) - head - tail
    return text[:head] + truncation_notice(omitted, len(text), label=label) + text[-tail:]
