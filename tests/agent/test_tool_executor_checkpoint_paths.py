"""Behavioral coverage for checkpoint preflight: path resolution and mutation decisions."""

from types import SimpleNamespace

import pytest

from agent.tool_executor import _ToolCallRef, _begin_tool_execution, _ensure_file_checkpoint
from tools.checkpoint_manager import CheckpointManager


def test_relative_file_checkpoint_uses_task_workspace(tmp_path, monkeypatch):
    """Checkpoint lookup must use the same cwd as a relative file mutation."""
    process_cwd = tmp_path / "opt" / "hermes"
    workspace_cwd = tmp_path / "opt" / "data" / "workspace"
    process_cwd.mkdir(parents=True)
    workspace_cwd.mkdir(parents=True)

    # Both directories contain content so checkpointing the wrong one would
    # still succeed and remain observable as the regression did in Docker.
    (process_cwd / "pyproject.toml").write_text("[project]\nname = 'hermes'\n")
    (workspace_cwd / "pyproject.toml").write_text("[project]\nname = 'workspace'\n")
    (workspace_cwd / "existing.txt").write_text("before\n")

    monkeypatch.chdir(process_cwd)
    monkeypatch.setenv("TERMINAL_CWD", str(workspace_cwd))
    monkeypatch.setattr(
        "tools.checkpoint_manager.CHECKPOINT_BASE",
        tmp_path / "checkpoints",
    )

    manager = CheckpointManager(enabled=True)
    agent = SimpleNamespace(_checkpoint_mgr=manager)

    _ensure_file_checkpoint(
        agent,
        "write_file",
        {"path": "test_permissions2.txt"},
        "gateway-session",
    )

    assert manager.list_checkpoints(str(workspace_cwd))
    assert manager.list_checkpoints(str(process_cwd)) == []


def _terminal_checkpoint_agent(manager: CheckpointManager) -> SimpleNamespace:
    """Minimal agent surface _begin_tool_execution touches before a terminal call runs."""
    return SimpleNamespace(
        _checkpoint_mgr=manager,
        quiet_mode=True,
        tool_progress_mode="off",
        verbose_logging=False,
        log_prefix_chars=80,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        _current_tool=None,
        _touch_activity=lambda *_args, **_kwargs: None,
    )


def _terminal_ref(command: str, workspace) -> _ToolCallRef:
    return _ToolCallRef(
        name="terminal",
        args={"command": command, "workdir": str(workspace)},
        task_id="task-checkpoint",
        call_id="call-checkpoint",
        trace=[],
    )


def _prepare_terminal_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("one\n", encoding="utf-8")
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", tmp_path / "checkpoints")
    return workspace, CheckpointManager(enabled=True)


@pytest.mark.parametrize("command", [
    "sed -i 's/one/two/' app.py",
    "sed --in-place 's/one/two/' app.py",
    "sed -Ei 's/one/two/' app.py",
    "sed -n -i 's/one/two/' app.py",
    "gsed -i 's/one/two/' app.py",
    "sed -i.bak 's/one/two/' app.py",
])
def test_terminal_inplace_edit_creates_pre_edit_checkpoint(tmp_path, monkeypatch, command):
    """The checkpoint preflight, not only the pattern helper, must snapshot before an in-place edit."""
    workspace, manager = _prepare_terminal_workspace(tmp_path, monkeypatch)
    agent = _terminal_checkpoint_agent(manager)

    _begin_tool_execution(agent, _terminal_ref(command, workspace), None)

    assert manager.list_checkpoints(str(workspace)), f"no pre-edit checkpoint for {command!r}"


@pytest.mark.parametrize("command", [
    "sed -n '1,5p' app.py",
    "sed 's/-i/foo/' app.py",
    "sed -e 's/x/y/' app.py",
])
def test_terminal_read_only_sed_does_not_create_checkpoint(tmp_path, monkeypatch, command):
    workspace, manager = _prepare_terminal_workspace(tmp_path, monkeypatch)
    agent = _terminal_checkpoint_agent(manager)

    _begin_tool_execution(agent, _terminal_ref(command, workspace), None)

    assert manager.list_checkpoints(str(workspace)) == []
