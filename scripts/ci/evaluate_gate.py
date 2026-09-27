#!/usr/bin/env python3
"""Evaluate the ``all-checks-pass`` gate job in ``.github/workflows/ci.yaml``.

A ``needs.<job>.result`` is one of ``success``, ``failure``, ``cancelled``, or ``skipped``.
``skipped`` passes the gate — most lanes are conditioned on ``needs.detect.outputs.*`` and
legitimately have nothing to do for a PR that didn't touch their area.

``HARD_DISABLED_LANES`` is a different case: these jobs are unconditionally ``if: false`` in
ci.yaml regardless of what changed, so every single run reports them ``skipped`` — treating
that identically to a real pass would let the gate report green while an entire suite never
once ran. Flag them distinctly in the printed summary instead. This does not change whether the
gate blocks a merge (re-enabling a lane that's off because it's flaky is a separate, larger
fix) — it only stops "skipped" from silently reading as "verified".

Usage (matches the ci.yaml step): pipe the ``toJSON(needs)`` object in on stdin.
    echo "$NEEDS" | python3 scripts/ci/evaluate_gate.py
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import suppress

# Keep in sync with ci.yaml: a job listed here must have a bare `if: false` there (not a
# `needs.detect.outputs.*` condition), and the value should say why it's off.
HARD_DISABLED_LANES = {
    "e2e-desktop": "disabled since Sep 2026 — still flaky after the Sep 1 re-enable attempt",
}


def evaluate(needs: dict) -> tuple[dict[str, str], list[str], list[str]]:
    """Return ``(compact result-map, blocking job names, hard-disabled job names)``.

    ``blocking`` = required jobs whose result is neither ``success`` nor ``skipped``.
    ``disabled`` = jobs in ``HARD_DISABLED_LANES`` whose result is ``skipped`` this run —
    i.e. reported as passing but never actually executed, by design rather than by relevance.
    """
    compact = {name: info["result"] for name, info in needs.items()}
    blocking = [name for name, result in compact.items() if result not in ("success", "skipped")]
    disabled = [name for name in HARD_DISABLED_LANES if compact.get(name) == "skipped"]
    return compact, blocking, disabled


def render(compact: dict[str, str], blocking: list[str], disabled: list[str]) -> str:
    lines = []
    for name, result in sorted(compact.items()):
        if name in disabled:
            lines.append(f"⚠️  {name}: skipped — HARD-DISABLED, not verified this run ({HARD_DISABLED_LANES[name]})")
        else:
            icon = "✅" if result in ("success", "skipped") else "❌"
            lines.append(f"{icon} {name}: {result}")
    if blocking:
        lines.append(f"::error::{len(blocking)} required job(s) did not complete successfully: {', '.join(blocking)}")
    else:
        lines.append("All required checks passed (or were not applicable)")
    return "\n".join(lines)


def main() -> int:
    # CI runners are UTF-8, but a non-UTF-8 host console (e.g. a developer running this
    # script directly on Windows) would otherwise crash on the ✅/❌/⚠️ glyphs below.
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError):
            stream.reconfigure(encoding="utf-8")
    needs = json.load(sys.stdin)
    compact, blocking, disabled = evaluate(needs)

    needs_json = json.dumps(compact)
    print(f"needs-json={needs_json}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"needs-json={needs_json}\n")

    print(render(compact, blocking, disabled))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
