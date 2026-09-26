"""Tests for the `hermes memory reset` CLI command."""

from types import SimpleNamespace

import pytest

from hermes_cli.main_agent_cmds import _cmd_memory_reset
from tools.memory_tool import MemoryStore


@pytest.fixture
def memory_env(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    (memories / "MEMORY.md").write_text(
        "§\nHermes repo is at ~/.hermes/hermes-agent\n§\nUser prefers dark themes",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "§\nUser is Teknium\n§\nTimezone: US Pacific",
        encoding="utf-8",
    )
    return hermes_home, memories


def _reset(target="all", yes=True):
    _cmd_memory_reset(SimpleNamespace(target=target, yes=yes))


class TestMemoryReset:
    def test_reset_all_with_yes_flag(self, memory_env):
        _home, memories = memory_env

        _reset("all")

        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()
        assert (memories / "MEMORY.md.reset-generation").read_text(encoding="utf-8").strip()
        assert (memories / "USER.md.reset-generation").read_text(encoding="utf-8").strip()

    def test_reset_no_files_still_advances_forget_generation(self, tmp_path, monkeypatch, capsys):
        hermes_home = tmp_path / ".hermes"
        memories = hermes_home / "memories"
        memories.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        _reset("all")

        out = capsys.readouterr().out
        assert "No built-in memory files are currently present" in out
        assert (memories / "MEMORY.md.reset-generation").read_text(encoding="utf-8").strip()
        assert (memories / "USER.md.reset-generation").read_text(encoding="utf-8").strip()

    def test_reset_partial_files_advances_both_requested_targets(self, memory_env):
        _home, memories = memory_env
        (memories / "USER.md").unlink()

        _reset("all")

        assert not (memories / "MEMORY.md").exists()
        assert (memories / "MEMORY.md.reset-generation").exists()
        assert (memories / "USER.md.reset-generation").exists()

    def test_cancel_does_not_advance_generation(self, memory_env, monkeypatch):
        _home, memories = memory_env
        monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "no")

        _reset("memory", yes=False)

        assert (memories / "MEMORY.md").exists()
        assert not (memories / "MEMORY.md.reset-generation").exists()

    def test_cli_reset_fences_already_loaded_store(self, memory_env):
        _home, memories = memory_env
        store = MemoryStore()
        store.load_from_disk()
        before = store.reset_generation("memory")

        _reset("memory")

        assert store.reset_generation("memory") == before
        blocked = store.add("memory", "stale session write")
        assert blocked["success"] is False
        assert blocked["reset_conflict"] is True
        assert not (memories / "MEMORY.md").exists()
