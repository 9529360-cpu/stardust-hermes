"""Tests for scripts/ci/evaluate_gate.py — the ``all-checks-pass`` gate in ci.yaml.

FAIL-BEFORE (class): the gate's aggregation treated every ``skipped`` job identically,
so a job that is unconditionally ``if: false`` (permanently disabled, never run) reported
the exact same green checkmark as a job that was legitimately not applicable to this PR.
Issue: CI gate can report required checks green while Desktop E2E is hard-disabled.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "evaluate_gate.py"
_spec = importlib.util.spec_from_file_location("evaluate_gate", _PATH)
if _spec is None or _spec.loader is None:
    raise ImportError("Failed to load evaluate_gate.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
evaluate = _mod.evaluate
render = _mod.render
HARD_DISABLED_LANES = _mod.HARD_DISABLED_LANES


def _needs(**results: str) -> dict:
    return {name: {"result": result, "outputs": {}} for name, result in results.items()}


def test_all_success_passes_with_no_blocking_or_disabled():
    compact, blocking, disabled = evaluate(_needs(detect="success", lint="success"))
    assert compact == {"detect": "success", "lint": "success"}
    assert blocking == []
    assert disabled == []


def test_legitimately_skipped_job_is_not_blocking_and_not_flagged_disabled():
    """A job skipped because this PR didn't touch its area (e.g. js-tests on a
    Python-only PR) passes the gate and is not treated as a hard-disabled lane."""
    compact, blocking, disabled = evaluate(_needs(detect="success", **{"js-tests": "skipped"}))
    assert blocking == []
    assert disabled == []


def test_hard_disabled_lane_skipped_passes_the_gate_but_is_flagged():
    """e2e-desktop's ci.yaml `if: false` makes it report `skipped` on every run — that
    must still not block the gate (this isn't about forcing a flaky suite back on),
    but it must be visibly distinguished from an ordinary irrelevant-to-this-PR skip."""
    compact, blocking, disabled = evaluate(_needs(detect="success", **{"e2e-desktop": "skipped"}))
    assert blocking == []
    assert disabled == ["e2e-desktop"]


def test_hard_disabled_lane_that_actually_ran_is_not_flagged():
    """If e2e-desktop is ever re-enabled and genuinely runs, its real result (success/
    failure) is reported normally — the disabled-flag only fires on a bare skip."""
    compact, blocking, disabled = evaluate(_needs(detect="success", **{"e2e-desktop": "success"}))
    assert blocking == []
    assert disabled == []


def test_failure_blocks_the_gate():
    compact, blocking, disabled = evaluate(_needs(detect="success", tests="failure"))
    assert blocking == ["tests"]


def test_cancelled_blocks_the_gate():
    compact, blocking, disabled = evaluate(_needs(detect="success", tests="cancelled"))
    assert blocking == ["tests"]


def test_render_marks_hard_disabled_lane_distinctly_from_pass_and_fail():
    compact, blocking, disabled = evaluate(_needs(detect="success", **{"e2e-desktop": "skipped"}))
    out = render(compact, blocking, disabled)
    assert "e2e-desktop: skipped" in out
    assert "HARD-DISABLED" in out
    assert "✅ e2e-desktop" not in out
    assert "All required checks passed" in out


def test_render_reports_blocking_failure_and_nonzero_intent():
    compact, blocking, disabled = evaluate(_needs(detect="success", tests="failure"))
    out = render(compact, blocking, disabled)
    assert "::error::1 required job(s) did not complete successfully: tests" in out
    assert "❌ tests: failure" in out


def test_hard_disabled_lanes_are_named_jobs_in_ci_yaml():
    """Keep the registry honest: every entry here must correspond to a job in ci.yaml
    whose `if:` is a bare `false` — not a `needs.detect.outputs.*` condition — so this
    list can't silently drift from what's actually hard-disabled."""
    ci_yaml = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yaml").read_text(encoding="utf-8")
    for job_name in HARD_DISABLED_LANES:
        marker = f"\n  {job_name}:\n"
        assert marker in ci_yaml, f"{job_name} is not a top-level job in ci.yaml"
        block = ci_yaml.split(marker, 1)[1].split("\n\n", 1)[0]
        assert "if: false" in block, f"{job_name} is listed as hard-disabled but ci.yaml doesn't bare-`if: false` it"
