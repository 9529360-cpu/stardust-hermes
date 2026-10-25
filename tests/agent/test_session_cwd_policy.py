"""Inherited cwd must not redirect a conversation's prompt or local tools."""
from types import SimpleNamespace

import pytest

from agent import runtime_cwd as rt
from tools import terminal_tool as tt
from tools.file_tools_paths import _resolve_path_for_task


@pytest.fixture(autouse=True)
def isolated_records(monkeypatch):
    import json
    from hermes_constants import get_hermes_home
    (get_hermes_home() / "config.yaml").write_text(json.dumps({"terminal": {"cwd_env_policy": "session_first"}}))
    monkeypatch.setattr(tt, "_session_cwd", {})
    token = rt.set_session_cwd(None)
    yield
    rt._SESSION_CWD.reset(token)


def test_polluted_parent_session_prompt_and_tools(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    foreign = tmp_path / "foreign"
    workspace.mkdir()
    foreign.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("TERMINAL_CWD", str(foreign))
    agent = SimpleNamespace(session_id="session", _initial_session_cwd=rt.initial_session_cwd())
    for i in range(5):
        with rt.conversation_cwd(agent, f"turn-{i}"):
            assert rt.resolve_agent_cwd() == workspace
            assert rt.resolve_context_cwd() == workspace
            assert tt.get_session_cwd(f"turn-{i}") == str(workspace)
            assert tt._resolve_config_cwd("local", False)[0] == str(workspace)
            assert _resolve_path_for_task("note.txt", f"turn-{i}") == workspace / "note.txt"
    assert rt.scope_terminal_cwd() == str(foreign)


@pytest.mark.parametrize("terminal", [{"cwd_env_policy": "env_first"}, {"cwd": "explicit"}])
def test_explicit_config_opt_in(monkeypatch, tmp_path, terminal):
    import json
    from hermes_constants import get_hermes_home
    foreign = tmp_path / "explicit"
    foreign.mkdir()
    if "cwd" in terminal:
        terminal["cwd"] = str(foreign)
    (get_hermes_home() / "config.yaml").write_text(json.dumps({"terminal": terminal}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMINAL_CWD", str(foreign))
    assert rt.initial_session_cwd() == str(foreign)


def test_no_session_still_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert rt.resolve_agent_cwd() == tmp_path


@pytest.mark.parametrize("kind", ["cron", "delegate"])
def test_pre_recorded_job_or_child_wins(monkeypatch, tmp_path, kind):
    workspace = tmp_path / kind
    workspace.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    tt.record_session_cwd(kind, str(workspace))
    agent = SimpleNamespace(session_id=kind, _initial_session_cwd=str(tmp_path))
    with rt.conversation_cwd(agent, kind):
        assert rt.resolve_agent_cwd() == workspace
        assert tt._resolve_config_cwd("local", False)[0] == str(workspace)
        assert _resolve_path_for_task("note.txt", kind) == workspace / "note.txt"


def test_conversation_wrapper_binds_before_prompt_and_restores(monkeypatch, tmp_path):
    from agent import conversation_loop as loop
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(session_id="wrapper", _initial_session_cwd=str(workspace))
    def turn(agent, *args, **kwargs):
        assert rt.resolve_agent_cwd() == workspace
        assert tt.get_session_cwd("task") == str(workspace)
        raise RuntimeError("cancelled")
    monkeypatch.setattr(loop, "_run_conversation_turn", turn)
    with pytest.raises(RuntimeError, match="cancelled"):
        loop.run_conversation(agent, "read", task_id="task")
    assert rt.resolve_agent_cwd() == tmp_path


def test_gateway_explicit_scope_wins_over_launch_and_parent(monkeypatch, tmp_path):
    from tools.terminal_scope import set_terminal_scope, reset_terminal_scope
    workspace = tmp_path / "gateway"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "foreign"))
    token = set_terminal_scope({"TERMINAL_ENV": "local", "TERMINAL_CWD": str(workspace)})
    try:
        agent = SimpleNamespace(session_id="gateway", _initial_session_cwd=rt.initial_session_cwd())
        with rt.conversation_cwd(agent, "gateway-turn"):
            assert rt.resolve_agent_cwd() == workspace
            assert _resolve_path_for_task("marker.txt", "gateway-turn") == workspace / "marker.txt"
    finally:
        reset_terminal_scope(token)


def test_remote_without_cwd_does_not_acquire_host_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    agent = SimpleNamespace(session_id="remote", _initial_session_cwd=rt.initial_session_cwd())
    assert agent._initial_session_cwd == ""
    with rt.conversation_cwd(agent, "remote-turn"):
        assert tt._resolve_config_cwd("ssh", False)[0] == "~"


def test_session_capture_survives_process_chdir(monkeypatch, tmp_path):
    workspace = tmp_path / "launch"
    later = tmp_path / "later"
    workspace.mkdir()
    later.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("TERMINAL_CWD", str(later))
    agent = SimpleNamespace(session_id="capture", _initial_session_cwd=rt.initial_session_cwd())
    monkeypatch.chdir(later)
    with rt.conversation_cwd(agent, "capture-turn"):
        assert rt.resolve_agent_cwd() == workspace
        assert _resolve_path_for_task("marker.txt", "capture-turn") == workspace / "marker.txt"
