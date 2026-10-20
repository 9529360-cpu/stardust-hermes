"""A checker after a passing test must not invalidate its own evidence."""
import json

import pytest

from agent.verification_evidence import terminal_workspace_mutation_root, verification_status
from agent.verification_stop import build_verify_on_stop_nudge
from tools.terminal_tool_result import finalize_foreground_result


@pytest.mark.parametrize("tail,writes", [
    ("ruff check source.py", False),
    ("ruff check --fix source.py", True),
    ("ruff check --fix-only source.py", True),
    ("ruff check --output-file=report.json source.py", True),
    ("ruff format --check source.py", False),
    ("ruff format source.py", True),
    ("black --check source.py", False),
    ("black source.py", True),
    ("isort --check-only source.py", False),
    ("isort source.py", True),
    ("unknown_module source.py", True),
])
def test_python_module_check_and_write_twins(tmp_path, monkeypatch, tail, writes):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    command = f'"D:/Python/python.exe" -m {tail}'
    assert bool(terminal_workspace_mutation_root(command=command, cwd=tmp_path, exit_code=0)) is writes


def test_passing_test_then_ruff_keeps_real_terminal_evidence_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    command = ('python -m pytest tests/test_example.py && '
               '"D:/Python/python.exe" -m ruff check source.py && git diff --check && git status --short')
    result = json.loads(finalize_foreground_result(
        command=command, result={"output": "1 passed\nAll checks passed!", "returncode": 0},
        env=object(), env_type="local", effective_task_id="task", task_id="task",
        session_id="s1", session_key="key", workdir=None,
        command_cwd=str(tmp_path), approval_note=None,
    ))
    assert "workspace_mutation" not in result
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "passed"
    assert build_verify_on_stop_nudge(session_id="s1", changed_paths=[str(tmp_path)]) is None
    # A later source-rewriting invocation must still expire the previous pass.
    finalize_foreground_result(
        command='python -m ruff check --fix source.py',
        result={"output": "fixed", "returncode": 0}, env=object(), env_type="local",
        effective_task_id="task", task_id="task", session_id="s1", session_key="key",
        workdir=None, command_cwd=str(tmp_path), approval_note=None,
    )
    assert verification_status(session_id="s1", cwd=tmp_path)["status"] == "stale"
