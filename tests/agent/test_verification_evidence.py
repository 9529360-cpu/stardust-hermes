import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.verification_evidence import (
    classify_verification_command,
    mark_workspace_edited,
    record_terminal_result,
    record_terminal_workspace_mutation,
    terminal_workspace_mutation_root,
    verification_status,
)

@pytest.fixture(autouse=True)
def _ledger_on(monkeypatch):
    """The ledger is inert unless verify-on-stop is enabled; these tests exercise the ledger."""
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")



def _node_project(root: Path) -> None:
    (root / "package.json").write_text(
        json.dumps({
            "scripts": {
                "test": "vitest", "lint": "eslint .", "build": "vite build",
                "format": "prettier --write .", "dev": "vite",
            }
        })
    )
    (root / "pnpm-lock.yaml").write_text("")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "run_tests.sh").write_text("#!/bin/sh\n")


def _python_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")








def test_lint_and_typecheck_are_not_reported_as_full_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)

    lint = classify_verification_command(
        "pnpm run lint",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )
    test = classify_verification_command(
        "pnpm run test -- tests/button.test.tsx",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert lint is not None
    assert lint.kind == "lint"
    assert lint.scope == "full"
    assert test is not None
    assert test.kind == "test"
    assert test.scope == "targeted"




def test_shell_wrappers_match_but_echo_does_not(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)

    wrapped = classify_verification_command(
        "env CI=1 bash scripts/run_tests.sh tests/test_widget.py",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )
    echoed = classify_verification_command(
        "echo scripts/run_tests.sh tests/test_widget.py",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert wrapped is not None
    assert wrapped.canonical_command == "scripts/run_tests.sh"
    assert wrapped.scope == "targeted"
    assert echoed is None


@pytest.mark.parametrize(
    "command",
    [
        "pytest || true",
        "pytest ; true",
        "pytest | tee test.log",
        "pytest &",
    ],
)
def test_masking_shell_control_is_not_verification_evidence(
    tmp_path, monkeypatch, command
):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    evidence = classify_verification_command(
        command,
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert evidence is None


@pytest.mark.parametrize("command", ["prepare && pytest", "pytest && report"])
def test_successful_and_chain_preserves_passing_evidence(
    tmp_path, monkeypatch, command
):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    evidence = classify_verification_command(
        command,
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert evidence is not None
    assert evidence.status == "passed"


@pytest.mark.parametrize("exit_code, expected", [(0, "passed"), (1, "failed")])
def test_final_verifier_after_sequence_owns_shell_exit_status(
    tmp_path, monkeypatch, exit_code, expected
):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    evidence = classify_verification_command(
        "prepare; pytest",
        cwd=tmp_path,
        session_id="s1",
        exit_code=exit_code,
    )

    assert evidence is not None
    assert evidence.status == expected


def test_quoted_shell_operator_remains_a_verifier_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    evidence = classify_verification_command(
        "pytest -k 'passes || fails'",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert evidence is not None
    assert evidence.status == "passed"


@pytest.mark.parametrize("redirect", ["2>&1", "&> test.log"])
def test_shell_redirection_does_not_hide_simple_verifier(tmp_path, monkeypatch, redirect):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    evidence = classify_verification_command(
        f"pytest {redirect}",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
    )

    assert evidence is not None
    assert evidence.status == "passed"


def test_masked_ad_hoc_script_is_not_verification_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    script = Path(tempfile.gettempdir()) / f"hermes-ad-hoc-{tmp_path.name}.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    try:
        evidence = classify_verification_command(
            f"python {script} || true",
            cwd=tmp_path,
            session_id="s1",
            exit_code=0,
        )
    finally:
        script.unlink(missing_ok=True)

    assert evidence is None


def test_masked_verifier_does_not_clear_edited_ledger_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)
    record_terminal_result(
        command="pytest",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
        output="passed",
    )
    mark_workspace_edited(
        session_id="s1",
        cwd=tmp_path,
        paths=[str(tmp_path / "changed.py")],
    )

    result = record_terminal_result(
        command="pytest || true",
        cwd=tmp_path,
        session_id="s1",
        exit_code=0,
        output="1 failed",
    )

    assert result is None
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "stale"


def test_terminal_script_after_passing_test_stales_evidence_and_stop_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)
    record_terminal_result(
        command="pnpm run test", cwd=tmp_path, session_id="s1", exit_code=0, output="passed",
    )

    mutation = record_terminal_workspace_mutation(
        command="python scripts/rewrite_sources.py", cwd=tmp_path, session_id="s1", exit_code=0,
    )

    assert mutation == {"root": str(tmp_path.resolve())}
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "stale"
    from agent.verification_stop import build_verify_on_stop_nudge

    nudge = build_verify_on_stop_nudge(session_id="s1", changed_paths=[mutation["root"]])
    assert nudge is not None
    assert "Verification status: stale" in nudge


def test_foreground_terminal_result_exposes_mutation_and_stales_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)
    record_terminal_result(
        command="pnpm run test", cwd=tmp_path, session_id="s1", exit_code=0, output="passed",
    )
    from tools.terminal_tool_result import finalize_foreground_result

    result = json.loads(finalize_foreground_result(
        command="python scripts/rewrite_sources.py",
        result={"output": "rewrote", "returncode": 0}, env=object(), env_type="local",
        effective_task_id="task", task_id="task", session_id="s1", session_key="key",
        workdir=None, command_cwd=str(tmp_path), approval_note=None,
    ))

    assert result["workspace_mutation"] == {"root": str(tmp_path.resolve())}
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "stale"


def test_compound_terminal_install_before_test_keeps_evidence_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)
    record_terminal_result(
        command="pnpm run test", cwd=tmp_path, session_id="s1", exit_code=0, output="previous pass",
    )

    while_running = terminal_workspace_mutation_root(
        command="pnpm install && pnpm run test", cwd=tmp_path, exit_code=0, verification_complete=False,
    )
    provisional = record_terminal_workspace_mutation(
        command="pnpm install && pnpm run test", cwd=tmp_path, session_id="s1", exit_code=0,
        verification_complete=False,
    )
    assert provisional == {"root": str(tmp_path.resolve())}
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "stale"

    evidence = record_terminal_result(
        command="pnpm install && pnpm run test", cwd=tmp_path, session_id="s1", exit_code=0, output="passed",
    )
    mutation = terminal_workspace_mutation_root(
        command="pnpm install && pnpm run test", cwd=tmp_path, exit_code=0,
    )

    assert evidence is not None and evidence["status"] == "passed"
    assert mutation is None
    assert while_running == str(tmp_path.resolve())
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "passed"


@pytest.mark.parametrize("command", [
    "git status --short",
    "git --version",
    "git -C /tmp/project status --short",
    "git diff --check",
    "npm test",
    "pnpm run test",
    "pnpm run build",
    "python -m pytest tests/test_example.py",
    "python tests/test_runner.py",
    "python -c 'print(1)'",
])
def test_read_only_test_and_build_terminal_commands_do_not_stale_evidence(tmp_path, monkeypatch, command):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)

    assert terminal_workspace_mutation_root(command=command, cwd=tmp_path, exit_code=0) is None


def test_inline_python_file_write_invalidates_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)

    assert terminal_workspace_mutation_root(
        command="python -c \"from pathlib import Path; Path('src.py').write_text('x')\"",
        cwd=tmp_path, exit_code=0,
    ) == str(tmp_path.resolve())




def test_temp_script_records_ad_hoc_evidence_without_canonical_suite(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    script = Path(tempfile.gettempdir()) / f"hermes-ad-hoc-{tmp_path.name}.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    try:
        evidence = classify_verification_command(
            f"python {script}",
            cwd=tmp_path,
            session_id="s1",
            exit_code=0,
            output="ok",
        )
    finally:
        script.unlink(missing_ok=True)

    assert evidence is not None
    assert evidence.canonical_command == "ad-hoc verification script"
    assert evidence.kind == "ad_hoc"
    assert evidence.scope == "targeted"
    assert evidence.status == "passed"












def test_file_tool_stales_evidence_by_session_id_for_absolute_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _node_project(tmp_path)
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()

    record_terminal_result(
        command="pnpm test",
        cwd=tmp_path,
        session_id="conversation",
        exit_code=0,
        output="green",
    )

    from tools.file_tools import write_file_tool

    result = json.loads(
        write_file_tool(
            str(target),
            "export const ok = true\n",
            task_id="turn",
            session_id="conversation",
        )
    )

    assert result["files_modified"] == [str(target.resolve())]
    assert verification_status(session_id="conversation", cwd=tmp_path)["status"] == "stale"
    assert verification_status(session_id="turn", cwd=tmp_path)["status"] == "unverified"






def test_recording_expires_old_edit_only_state(tmp_path, monkeypatch):
    home = tmp_path / ".stardust"
    monkeypatch.setenv("STARDUST_HOME", str(home))
    _node_project(tmp_path)

    mark_workspace_edited(
        session_id="old-session",
        cwd=tmp_path,
        paths=[str(tmp_path / "src" / "app.ts")],
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with sqlite3.connect(home / "verification_evidence.db") as conn:
        conn.execute("UPDATE verification_state SET last_edit_at = ?", (cutoff,))
        conn.commit()

    record_terminal_result(
        command="pnpm test",
        cwd=tmp_path,
        session_id="new-session",
        exit_code=0,
        output="new green",
    )

    status = verification_status(session_id="old-session", cwd=tmp_path)
    assert status["status"] == "unverified"
    assert status["changed_paths"] == []


def test_windows_backslash_ad_hoc_script_path_is_matched(tmp_path, monkeypatch):
    """Ad-hoc verification scripts with Windows backslash paths must be
    matched by ``_find_ad_hoc_match`` trying ``posix=False`` in addition to
    the default ``posix=True``. (#53553 / #65919)

    On Linux, ``Path`` doesn't parse Windows backslash paths, so we mock
    ``_is_temp_script_path`` to simulate the Windows environment where the
    path resolves correctly. The test verifies the posix=False splitting
    fallback — the actual fix from #53553.
    """
    from agent.verification_evidence import _find_ad_hoc_match

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    # On Windows, shlex.split(posix=True) eats backslashes as escape chars;
    # posix=False preserves them. Mock _is_temp_script_path so the test
    # focuses on the splitting fallback without needing a real Windows FS.
    def mock_is_temp_script(token, root):
        return "hermes-ad-hoc" in token and ".py" in token

    monkeypatch.setattr(
        "agent.verification_evidence._is_temp_script_path",
        mock_is_temp_script,
    )

    win_script = r"C:\Users\test\AppData\Local\Temp\hermes-ad-hoc-check.py"
    result = _find_ad_hoc_match(f"python {win_script}", tmp_path)
    assert result is not None, (
        "Windows backslash path should be matched via posix=False fallback"
    )


@pytest.mark.parametrize("command", [
    "touch src/new.py",
    "echo 'x = 2' >> src/mod.py",
    "printf 'x = 2' | tee src/mod.py",
    "echo note | tee -a notes.txt",
    "mkdir -p build/out",
    "ln -s src/mod.py src/link.py",
    "patch -p1 < fix.diff",
    "perl -pi -e 's/1/2/' src/mod.py",
    "black .",
    "ruff format src",
    "isort src",
    "cargo fmt",
    "Set-Content src/mod.py 'x'",
    "New-Item src/n.py -ItemType File",
    "Remove-Item src/mod.py",
    "del src\mod.py",
])
def test_common_shell_writers_invalidate_evidence(tmp_path, monkeypatch, command):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    assert terminal_workspace_mutation_root(command=command, cwd=tmp_path, exit_code=0) == str(tmp_path.resolve())


@pytest.mark.parametrize("command", [
    "echo hello > /dev/null",
    "python -m pytest tests/test_example.py 2>/dev/null",
    "python -m pytest tests/test_example.py > /dev/null 2>&1",
    "python -m pytest tests/test_example.py 2>&1 | tee test.log",
    "black --check .",
    "ruff format --check src",
    "cargo fmt -- --check",
    "git log --oneline > /dev/null",
    "ls | tee",
])
def test_null_sinks_check_only_formatters_and_verifier_capture_keep_evidence_fresh(tmp_path, monkeypatch, command):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))
    _python_project(tmp_path)

    assert terminal_workspace_mutation_root(command=command, cwd=tmp_path, exit_code=0) is None


def test_ledger_clock_is_strictly_increasing_even_when_the_wall_clock_repeats(monkeypatch):
    """Staleness compares ledger stamps, so equal stamps from a coarse clock must never occur."""
    import agent.verification_evidence as ledger

    frozen = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(ledger, "datetime", _FrozenDatetime)
    monkeypatch.setattr(ledger, "_last_stamp", None)

    stamps = [ledger.ledger_timestamp() for _ in range(50)]

    assert stamps == sorted(set(stamps))
    assert datetime.fromisoformat(stamps[-1]) - datetime.fromisoformat(stamps[0]) < timedelta(milliseconds=1)
