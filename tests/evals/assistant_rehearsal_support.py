"""Shared assertions for the end-to-end rehearsal tests: which required checks failed, and — when a
rehearsal does fail — the evidence behind every check plus the turns the backend actually produced."""


def failed(result):
    return sorted(c.key for c in result.verdict.checks if c.required and not c.ok)


def explain(result):
    lines = [f"status={result.verdict.status} note={result.verdict.note!r} artifacts={result.artifacts}"]
    lines += [f"  check {c.key} ok={c.ok} required={c.required}: {c.evidence}" for c in result.verdict.checks]
    lines += [f"  turn {t.index} {t.origin} {t.duration}s: {t.text[:300]!r}" for t in result.trace.turns]
    lines += [f"  tool {c.name} {c.args!r:.160} -> {(c.result_text or '')[:200]!r}" for c in result.trace.tool_calls]
    return "\n".join(lines)
