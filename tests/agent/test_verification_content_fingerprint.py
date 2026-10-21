"""A conservative "possible mutation" must not demand the same checks again when the tree is unchanged.

Live incident (2026-10-21): read-only commands such as ``python -c "...subprocess.check_output(['git','diff'])"``
or ``node <temp>/inspect.cjs`` were flagged as workspace mutations, the stop gate fired after an already
verified answer, and the agent re-ran its suites (9 nudges in one session)."""

import json
import subprocess
from pathlib import Path

import pytest

from agent.verification_evidence import (
    record_terminal_result,
    record_terminal_workspace_mutation,
    verification_status,
)
from agent.verification_stop import build_verify_on_stop_nudge
from agent.workspace_fingerprint import workspace_content_fingerprint

READ_ONLY_BUT_FLAGGED = (
    "python -c \"import subprocess;print(subprocess.check_output(['git','diff','--name-only']))\""
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.ts").write_text("export const a = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (root / "src" / "app.ts").write_text("export const a = 2\n", encoding="utf-8")  # uncommitted candidate
    return root


def _pass(root: Path) -> None:
    record_terminal_result(command="pnpm test", cwd=root, session_id="s", exit_code=0, output="green")


def test_flagged_read_only_command_keeps_pass_when_tree_unchanged(repo):
    _pass(repo)
    assert record_terminal_workspace_mutation(command=READ_ONLY_BUT_FLAGGED, cwd=repo, session_id="s", exit_code=0)

    status = verification_status(session_id="s", cwd=repo)
    assert status["status"] == "passed"
    assert build_verify_on_stop_nudge(session_id="s", changed_paths=[str(repo)]) is None


@pytest.mark.parametrize("change", ["tracked", "untracked"])
def test_real_change_after_pass_is_still_stale(repo, change):
    _pass(repo)
    record_terminal_workspace_mutation(command=READ_ONLY_BUT_FLAGGED, cwd=repo, session_id="s", exit_code=0)
    target = repo / "src" / ("app.ts" if change == "tracked" else "new.ts")
    target.write_text("export const a = 3\n", encoding="utf-8")

    assert verification_status(session_id="s", cwd=repo)["status"] == "stale"
    assert build_verify_on_stop_nudge(session_id="s", changed_paths=[str(repo)]) is not None


def test_failed_run_is_never_upgraded(repo):
    record_terminal_result(command="pnpm test", cwd=repo, session_id="s", exit_code=1, output="red")
    record_terminal_workspace_mutation(command=READ_ONLY_BUT_FLAGGED, cwd=repo, session_id="s", exit_code=0)
    assert verification_status(session_id="s", cwd=repo)["status"] == "stale"


def test_non_git_workspace_has_no_fingerprint(tmp_path):
    assert workspace_content_fingerprint(tmp_path) is None
    assert workspace_content_fingerprint(None) is None
