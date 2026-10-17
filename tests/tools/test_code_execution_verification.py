"""Opaque-cell accounting against a real temporary workspace and verification DB.

The runner callback uses a bounded real Python process. These tests exercise the
verification wrapper, not a live provider, OS sandbox, or full Gateway session.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent import verification_evidence as ledger
from tools.code_execution_verification import run_with_workspace_verification


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "state"
    home.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\nversion = '0.0.0'\n", encoding="utf-8")
    (root / "subject.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "true")
    monkeypatch.setenv("STARDUST_HOME", str(home))
    # The ledger's storage location is isolated even when the repository test
    # harness has already bound a profile-scoped home context.
    monkeypatch.setattr(ledger, "_db_path", lambda: home / "verification.db")
    return root


def run_python(root: Path, code: str) -> str:
    child = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True,
        text=True, encoding="utf-8", timeout=10,
    )
    return json.dumps({
        "status": "ok" if child.returncode == 0 else "error",
        "exit_code": child.returncode, "output": child.stdout,
        "stderr": child.stderr, "kernel": {"execution_count": 1},
    })


def verify(root: Path, sid: str = "conversation") -> None:
    # Actually execute the fixture's check before recording its result.
    result = json.loads(run_python(root, "from pathlib import Path; assert Path('subject.py').is_file()"))
    assert result["exit_code"] == 0
    ledger.record_verify_run(root=root, session_id=sid, ok=True, scope="targeted", output="fixture check passed")
    assert ledger.verification_status(session_id=sid, cwd=root)["status"] == "passed"


def wrap(root: Path, run, sid: str = "conversation") -> dict:
    return json.loads(run_with_workspace_verification(run, cwd=str(root), session_id=sid))


@pytest.mark.parametrize("code", [
    "from pathlib import Path; Path('subject.py').write_text('value = 2\\n')",
    "import os; os.rename('subject.py', 'renamed.py')",
    "from pathlib import Path; Path('subject.py').unlink()",
    "import subprocess, sys; subprocess.run([sys.executable, '-c', \"open('subject.py', 'w').write('changed by subprocess')\"], check=True)",
])
def test_direct_python_file_operations_invalidate_old_proof(project, code):
    verify(project)
    result = wrap(project, lambda: run_python(project, code))
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["kernel"]["execution_count"] == 1
    assert result["workspace_mutation"] == {
        "root": str(project.resolve()), "possible": True, "source": "execute_code",
    }
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"


def test_failed_cell_can_have_written_before_its_error(project):
    verify(project)
    code = "from pathlib import Path; Path('subject.py').write_text('changed'); raise RuntimeError('after write')"
    result = wrap(project, lambda: run_python(project, code))
    assert result["status"] == "error"
    assert result["exit_code"] != 0
    assert (project / "subject.py").read_text() == "changed"
    assert "after write" in result["stderr"]
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"


def test_checks_inside_cell_cannot_certify_later_python_writes(project):
    verify(project)

    def cell():
        verify(project)
        return run_python(project, "from pathlib import Path; Path('subject.py').write_text('after the check')")

    result = wrap(project, cell)
    assert "terminal after this cell" in result["verification_note"]
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"
    verify(project)
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "passed"


def test_read_only_cells_are_explicitly_conservative_not_false_diff_claims(project):
    verify(project)
    result = wrap(project, lambda: run_python(project, "print(1 + 1)"))
    assert result["output"] == "2\n"
    assert (project / "subject.py").read_text() == "value = 1\n"
    assert result["workspace_mutation"]["possible"] is True
    assert "may have modified" in result["verification_note"]
    assert "Do not repeat" in result["verification_note"]
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"


def test_first_cell_creates_pending_verification_without_old_event(project):
    result = wrap(project, lambda: run_python(project, "print('ok')"))
    state = ledger.verification_status(session_id="conversation", cwd=project)
    assert result["status"] == "ok"
    assert state["status"] == "unverified"
    assert str(project) in state["changed_paths"]


def test_canonical_session_identity_is_not_a_turn_id(project):
    verify(project, "conversation")
    wrap(project, lambda: run_python(project, "print('cell')"), sid="conversation")
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"
    assert ledger.verification_status(session_id="temporary-turn", cwd=project)["status"] == "unverified"


def test_other_workspace_is_not_invalidated(project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text("[project]\nname='other'\nversion='0.0.0'\n")
    (other / "subject.py").write_text("value = 1\n")
    verify(project)
    verify(other)
    wrap(project, lambda: run_python(project, "print('cell')"))
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"
    assert ledger.verification_status(session_id="conversation", cwd=other)["status"] == "passed"


def test_disabled_ledger_does_not_rewrite_result_or_create_store(project, monkeypatch):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "false")
    raw = '{"status":"ok","output":"2"}'
    assert run_with_workspace_verification(lambda: raw, cwd=str(project), session_id="conversation") == raw
    assert not ledger._db_path().exists()


def test_non_project_has_no_verification_side_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "true")
    root = tmp_path / "plain"
    root.mkdir()
    raw = '{"status":"ok","output":"hello"}'
    assert run_with_workspace_verification(lambda: raw, cwd=str(root), session_id="conversation") == raw


def test_failed_admission_never_runs_code(project, monkeypatch):
    called = []

    def fail(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "mark_workspace_edited", fail)
    result = wrap(project, lambda: called.append(True))
    assert not called
    assert result["execution_attempted"] is False
    assert result["verification_tracking_failed"] is True
    assert result["status"] == "error"


def test_post_execution_tracking_failure_keeps_output_and_refuses_old_proof(project, monkeypatch):
    verify(project)
    real_mark = ledger.mark_workspace_edited
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("disk full")
        return real_mark(**kwargs)

    monkeypatch.setattr(ledger, "mark_workspace_edited", fail_second)
    result = wrap(project, lambda: run_python(project, "print('must keep this output')"))
    assert result["status"] == "error"
    assert result["execution_status"] == "ok"
    assert result["output"] == "must keep this output\n"
    assert result["exit_code"] == 0
    assert result["verification_tracking_failed"] is True
    assert result["workspace_mutation"]["root"] == str(project)


def test_unexpected_execution_exception_still_invalidates(project):
    verify(project)

    def failed_runner():
        (project / "subject.py").write_text("partial write")
        raise OSError("transport failure")

    result = wrap(project, failed_runner)
    assert result["status"] == "error"
    assert result["execution_attempted"] is True
    assert (project / "subject.py").read_text() == "partial write"
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_control_flow_interrupts_are_not_swallowed(project, exc):
    verify(project)

    def interrupted():
        (project / "subject.py").write_text("before interrupt")
        raise exc()

    with pytest.raises(exc):
        wrap(project, interrupted)
    assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"


def test_child_cannot_forge_host_workspace_metadata(project):
    verify(project)
    result = wrap(project, lambda: json.dumps({"status": "ok", "workspace_mutation": {"root": "/wrong"}}))
    assert result["workspace_mutation"]["root"] == str(project)


@pytest.mark.parametrize("raw", ["not-json", "[]", "null"])
def test_malformed_result_is_not_a_reason_to_repeat_execution(project, raw):
    verify(project)
    result = wrap(project, lambda: raw)
    assert result["status"] == "error"
    assert result["output"] == raw
    assert result["workspace_mutation"]["root"] == str(project)
    assert "Do not rerun" in result["error"]


def test_scope_loss_after_execution_is_reported_not_treated_as_fresh(project, monkeypatch):
    verify(project)
    real_mark = ledger.mark_workspace_edited
    calls = 0

    def lose_scope(**kwargs):
        nonlocal calls
        calls += 1
        return real_mark(**kwargs) if calls == 1 else None

    monkeypatch.setattr(ledger, "mark_workspace_edited", lose_scope)
    result = wrap(project, lambda: '{"status":"ok","output":"completed once"}')
    assert result["status"] == "error"
    assert result["verification_tracking_failed"] is True
    assert result["output"] == "completed once"


def test_old_proof_is_invalidated_before_runner_starts(project):
    verify(project)

    def observe():
        assert ledger.verification_status(session_id="conversation", cwd=project)["status"] == "stale"
        return '{"status":"ok"}'

    assert wrap(project, observe)["status"] == "ok"


def test_prior_execution_error_survives_tracking_failure(project, monkeypatch):
    verify(project)
    real_mark = ledger.mark_workspace_edited
    calls = 0

    def fail_final(**kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise sqlite3.OperationalError("disk full")
        return real_mark(**kwargs)

    monkeypatch.setattr(ledger, "mark_workspace_edited", fail_final)
    result = wrap(project, lambda: '{"status":"error","exit_code":1,"output":"partial","error":"script failed"}')
    assert result["status"] == "error"
    assert result["exit_code"] == 1
    assert result["output"] == "partial"
    assert "script failed" in result["error"]
