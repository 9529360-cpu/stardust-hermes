"""Regression: a compression session-id rotation must not make already-passed
verification look missing, or verify-on-stop re-asks the model to re-run tests,
ruff and a commit/worktree check it just did (repeated "re-verify" reminders)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.conversation_compression import _carry_session_state_to_child
from agent.verification_evidence import (
    carry_verification_state,
    mark_workspace_edited,
    record_terminal_result,
    verification_status,
)
from agent.verification_stop import build_verify_on_stop_nudge


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    return root


def _edit_and_verify(root: Path, sid: str) -> str:
    changed = str(root / "src" / "app.ts")
    mark_workspace_edited(session_id=sid, cwd=root, paths=[changed])
    record_terminal_result(command="pnpm test", cwd=root, session_id=sid, exit_code=0, output="green")
    return changed


def test_rotation_without_carry_reproduces_the_false_nudge(project):
    changed = _edit_and_verify(project, "parent")
    assert build_verify_on_stop_nudge(session_id="parent", changed_paths=[changed]) is None
    # The bug: the child id has no ledger row, so passed work reads "unverified".
    assert build_verify_on_stop_nudge(session_id="child", changed_paths=[changed]) is not None


def test_compression_rotation_carries_verified_state(project):
    changed = _edit_and_verify(project, "parent")
    agent = SimpleNamespace(session_id="child", _session_db=None)

    _carry_session_state_to_child(agent, "parent", None)

    assert verification_status(session_id="child", cwd=project)["status"] == "passed"
    assert build_verify_on_stop_nudge(session_id="child", changed_paths=[changed], nudged_states=set()) is None


def test_carry_keeps_stale_state_stale_and_child_rows_win(project):
    changed = str(project / "src" / "app.ts")
    record_terminal_result(command="pnpm test", cwd=project, session_id="parent", exit_code=0, output="green")
    mark_workspace_edited(session_id="parent", cwd=project, paths=[changed])  # edit AFTER the pass
    carry_verification_state(from_session_id="parent", to_session_id="child")
    assert verification_status(session_id="child", cwd=project)["status"] == "stale"

    record_terminal_result(command="pnpm test", cwd=project, session_id="child", exit_code=0, output="green")
    assert carry_verification_state(from_session_id="parent", to_session_id="child") == 0
    assert verification_status(session_id="child", cwd=project)["status"] == "passed"


def test_carry_is_a_noop_for_same_or_missing_ids(project):
    assert carry_verification_state(from_session_id="a", to_session_id="a") == 0
    assert carry_verification_state(from_session_id=None, to_session_id="b") == 0
