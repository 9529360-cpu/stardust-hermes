"""profiles.configure authorizes a project folder as the profile's ``terminal.cwd``.

The Bots create flow and the editor send ``terminal_cwd``. An existing folder is written to
``terminal.cwd`` (where the agent's terminal and file work start); an empty value restores the
default ``.``; a folder that does not exist is refused, and nothing is written.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import tui_gateway.server as srv


@pytest.fixture
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def _configure(params):
    return srv._methods["profiles.configure"]("configure", {"name": "default", **params})["result"]


def _terminal_cwd(home: Path):
    cfg_path = home / "config.yaml"
    if not cfg_path.is_file():
        return None
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return (cfg.get("terminal") or {}).get("cwd")


def test_existing_folder_is_written_as_the_terminal_cwd(home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = _configure({"terminal_cwd": str(repo)})

    assert result["applied"].get("terminal_cwd") is True
    assert _terminal_cwd(home) == str(repo.resolve())


def test_missing_folder_is_refused_and_nothing_is_written(home, tmp_path):
    result = _configure({"terminal_cwd": str(tmp_path / "does-not-exist")})

    assert result["applied"].get("terminal_cwd") is False
    assert _terminal_cwd(home) is None


def test_empty_value_restores_the_default_cwd(home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _configure({"terminal_cwd": str(repo)})

    result = _configure({"terminal_cwd": ""})

    assert result["applied"].get("terminal_cwd") is True
    assert _terminal_cwd(home) == "."


def test_authorizing_a_folder_keeps_other_terminal_settings(home, tmp_path):
    cfg_path = home / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"terminal": {"backend": "local", "timeout": 90}}))
    repo = tmp_path / "repo"
    repo.mkdir()

    _configure({"terminal_cwd": str(repo)})

    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["terminal"]["cwd"] == str(repo.resolve())
    assert cfg["terminal"]["backend"] == "local"
    assert cfg["terminal"]["timeout"] == 90
