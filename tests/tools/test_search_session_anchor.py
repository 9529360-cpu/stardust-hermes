"""Opt-in search roots follow read_file across sequential local sessions."""
import json
from types import SimpleNamespace

import pytest

from agent import runtime_cwd as rt
from hermes_constants import get_hermes_home
from tools import file_tools as ft, terminal_tool as tt
from tools.file_operations import ShellFileOperations
from tools.environments.local import LocalEnvironment


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, name in ((a, "only-in-a.txt"), (b, "only-in-b.txt")):
        root.mkdir()
        (root / name).write_text(name)
    monkeypatch.chdir(a)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(tt, "_session_cwd", {})
    monkeypatch.setattr(tt, "_active_environments", {})
    monkeypatch.setattr(ft, "_file_ops_cache", {})
    monkeypatch.setattr(ft, "_read_tracker", {})
    env = LocalEnvironment(cwd=str(a))
    ops = ShellFileOperations(env, cwd=str(a))
    # Model the shared cached backend: its cwd remains the first session's.
    monkeypatch.setattr(ft, "_get_file_ops", lambda task_id: ops)
    yield a, b, ops
    env.cleanup()


def configure(flag):
    tools = {} if flag is None else {"file_search_session_anchor": flag}
    (get_hermes_home() / "config.yaml").write_text(json.dumps({
        "tools": tools, "terminal": {"cwd_env_policy": "session_first"}}))


def search(task_id):
    from tools.registry import registry
    return json.loads(registry.dispatch(
        "search_files", {"pattern": "only-in-*.txt", "target": "files"}, task_id=task_id))


@pytest.mark.parametrize("flag", [None, False, True])
def test_relative_search_uses_session_root(workspaces, flag):
    a, b, _ = workspaces
    configure(flag)
    tt.record_session_cwd("session-b", str(b))
    result = search("session-b")
    expected = "only-in-b.txt" if flag else "only-in-a.txt"
    assert result["total_count"] == 1, result
    assert expected in str(result), result


def test_two_sequential_sessions_never_see_each_others_files(workspaces, monkeypatch):
    a, b, _ = workspaces
    configure(True)
    for session, root, own, foreign in (
        ("u11", a, "only-in-a.txt", "only-in-b.txt"),
        ("u12", b, "only-in-b.txt", "only-in-a.txt"),
    ):
        monkeypatch.chdir(root)
        agent = SimpleNamespace(session_id=session, _initial_session_cwd=rt.initial_session_cwd())
        with rt.conversation_cwd(agent, session):
            result = search(session)
            assert own in str(result), result
            assert foreign not in str(result), result


def test_default_and_explicit_off_are_byte_identical(workspaces):
    _, b, _ = workspaces
    tt.record_session_cwd("off", str(b))
    configure(None)
    before = ft.search_tool("only-in-*.txt", target="files", task_id="off")
    ft._read_tracker.clear()
    configure(False)
    after = ft.search_tool("only-in-*.txt", target="files", task_id="off")
    assert before == after


@pytest.mark.parametrize("backend", ["ssh", "docker", "modal", "daytona", "singularity"])
def test_remote_search_path_text_unchanged(workspaces, monkeypatch, backend):
    _, b, ops = workspaces
    configure(True)
    tt.record_session_cwd("remote", str(b))
    env_class = type(backend.title() + "Environment", (), {})
    ops.env = env_class()
    received = []
    from tools.file_operations import SearchResult

    def capture(**kwargs):
        received.append(kwargs["path"])
        return SearchResult()

    monkeypatch.setattr(ops, "search", capture)
    search("remote")
    assert received == ["."]
