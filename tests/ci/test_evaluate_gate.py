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
DESKTOP_RUNTIME_PROOF_LANE = _mod.DESKTOP_RUNTIME_PROOF_LANE
DESKTOP_RUNTIME_PROOF_OUTPUTS = _mod.DESKTOP_RUNTIME_PROOF_OUTPUTS


def _needs(**results: str) -> dict:
    needs = {name: {"result": result, "outputs": {}} for name, result in results.items()}
    if "detect" in needs:
        needs["detect"]["outputs"] = {
            "python_prod": "false",
            "frontend": "false",
        }
    return needs


def _desktop_needs(
    *, python_prod: str = "false", frontend: str = "false", smoke: str = "skipped"
) -> dict:
    needs = _needs(
        detect="success",
        **{
            DESKTOP_RUNTIME_PROOF_LANE: smoke,
            "e2e-desktop": "skipped",
        },
    )
    needs["detect"]["outputs"] = {
        "python_prod": python_prod,
        "frontend": frontend,
    }
    return needs


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


def test_frontend_change_requires_successful_desktop_runtime_proof():
    compact, blocking, disabled = evaluate(
        _desktop_needs(frontend="true", smoke="skipped")
    )
    assert compact[DESKTOP_RUNTIME_PROOF_LANE] == "skipped"
    assert blocking == [DESKTOP_RUNTIME_PROOF_LANE]
    assert disabled == ["e2e-desktop"]


def test_python_runtime_change_requires_successful_desktop_runtime_proof():
    _compact, blocking, _disabled = evaluate(
        _desktop_needs(python_prod="true", smoke="skipped")
    )
    assert blocking == [DESKTOP_RUNTIME_PROOF_LANE]


def test_desktop_change_passes_with_visual_smoke_while_full_e2e_is_quarantined():
    compact, blocking, disabled = evaluate(
        _desktop_needs(frontend="true", smoke="success")
    )
    assert compact[DESKTOP_RUNTIME_PROOF_LANE] == "success"
    assert blocking == []
    assert disabled == ["e2e-desktop"]


def test_non_desktop_change_may_legitimately_skip_visual_smoke():
    _compact, blocking, disabled = evaluate(_desktop_needs(smoke="skipped"))
    assert blocking == []
    assert disabled == ["e2e-desktop"]


def test_unknown_desktop_classifier_outputs_fail_closed_to_runtime_proof():
    needs = _desktop_needs(smoke="skipped")
    needs["detect"]["outputs"] = {"python_prod": "", "frontend": "false"}
    _compact, blocking, _disabled = evaluate(needs)
    assert blocking == [DESKTOP_RUNTIME_PROOF_LANE]


def test_render_marks_required_skipped_runtime_proof_as_failure():
    compact, blocking, disabled = evaluate(
        _desktop_needs(frontend="true", smoke="skipped")
    )
    out = render(compact, blocking, disabled)
    assert f"❌ {DESKTOP_RUNTIME_PROOF_LANE}: skipped" in out
    assert f"✅ {DESKTOP_RUNTIME_PROOF_LANE}: skipped" not in out
    assert "::error::1 required job(s) did not complete successfully" in out


def test_hard_disabled_lane_skipped_passes_the_gate_but_is_flagged():
    """e2e-desktop's ci.yaml `if: false` makes it report `skipped` on every run — that
    must still not block the gate (this isn't about forcing a flaky suite back on),
    but it must be visibly distinguished from an ordinary irrelevant-to-this-PR skip."""
    compact, blocking, disabled = evaluate(
        _needs(detect="success", **{"e2e-desktop": "skipped"})
    )
    assert blocking == []
    assert disabled == ["e2e-desktop"]


def test_hard_disabled_lane_that_actually_ran_is_not_flagged():
    """If e2e-desktop is ever re-enabled and genuinely runs, its real result (success/
    failure) is reported normally — the disabled-flag only fires on a bare skip."""
    compact, blocking, disabled = evaluate(
        _needs(detect="success", **{"e2e-desktop": "success"})
    )
    assert blocking == []
    assert disabled == []


def test_failure_blocks_the_gate():
    compact, blocking, disabled = evaluate(_needs(detect="success", tests="failure"))
    assert blocking == ["tests"]


def test_cancelled_blocks_the_gate():
    compact, blocking, disabled = evaluate(_needs(detect="success", tests="cancelled"))
    assert blocking == ["tests"]


def test_render_marks_hard_disabled_lane_distinctly_from_pass_and_fail():
    compact, blocking, disabled = evaluate(
        _needs(detect="success", **{"e2e-desktop": "skipped"})
    )
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


def test_desktop_runtime_proof_contract_matches_ci_yaml():
    ci_yaml = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yaml").read_text(
        encoding="utf-8"
    )
    marker = f"\n  {DESKTOP_RUNTIME_PROOF_LANE}:\n"
    assert marker in ci_yaml
    block = ci_yaml.split(marker, 1)[1].split("\n\n", 1)[0]
    for output in DESKTOP_RUNTIME_PROOF_OUTPUTS:
        assert f"needs.detect.outputs.{output} == 'true'" in block

    gate = ci_yaml.split("\n  all-checks-pass:\n", 1)[1]
    assert f"      - {DESKTOP_RUNTIME_PROOF_LANE}\n" in gate.split("\n    if: always()", 1)[0]


def test_hard_disabled_lanes_are_named_jobs_in_ci_yaml():
    """Keep the registry honest: every entry here must correspond to a job in ci.yaml
    whose `if:` is a bare `false` — not a `needs.detect.outputs.*` condition — so this
    list can't silently drift from what's actually hard-disabled."""
    ci_yaml = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yaml"
    ).read_text(encoding="utf-8")
    for job_name in HARD_DISABLED_LANES:
        marker = f"\n  {job_name}:\n"
        assert marker in ci_yaml, f"{job_name} is not a top-level job in ci.yaml"
        block = ci_yaml.split(marker, 1)[1].split("\n\n", 1)[0]
        assert "if: false" in block, f"{job_name} is listed as hard-disabled but ci.yaml doesn't bare-`if: false` it"
