"""Session-bound config.set persists into the owning session profile."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import tui_gateway.server as server


def _write_cfg(home: Path, busy: str, approvals: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"display": {"busy_input_mode": busy}, "approvals": {"mode": approvals}}),
        encoding="utf-8",
    )


def _read(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    launch, worker = tmp_path / "launch", tmp_path / "profiles" / "worker"
    _write_cfg(launch, "queue", "manual")
    _write_cfg(worker, "queue", "manual")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setattr(server, "_hermes_home", launch)
    monkeypatch.setattr(server, "_cfg_cache", None)
    monkeypatch.setattr(server, "_cfg_sig", None)
    monkeypatch.setattr(server, "_cfg_path", None)
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    return launch, worker


@pytest.mark.parametrize(
    ("key", "value", "section", "field"),
    [("busy", "steer", "display", "busy_input_mode"), ("approval_mode", "off", "approvals", "mode")],
)
def test_session_bound_config_set_writes_session_profile(homes, key, value, section, field):
    launch, worker = homes
    session = {"agent": None, "profile_home": str(worker), "session_key": "worker-session"}
    with patch.dict(server._sessions, {"s-worker": session}, clear=False):
        resp = server._methods["config.set"](
            "rid", {"session_id": "s-worker", "key": key, "value": value}
        )
    assert resp["result"]["value"] == value
    assert _read(worker)[section][field] == value
    assert _read(launch)[section][field] != value


def test_unbound_config_set_still_writes_launch_profile(homes):
    launch, worker = homes
    resp = server._methods["config.set"]("rid", {"key": "busy", "value": "steer"})
    assert resp["result"]["value"] == "steer"
    assert _read(launch)["display"]["busy_input_mode"] == "steer"
    assert _read(worker)["display"]["busy_input_mode"] == "queue"


def test_profile_bound_cwd_does_not_publish_process_terminal_cwd(homes, tmp_path, monkeypatch):
    _launch, worker = homes
    cwd = tmp_path / "worker-cwd"
    cwd.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", "launch-cwd")
    session = {"agent": None, "profile_home": str(worker), "session_key": "worker-session"}
    with patch.dict(server._sessions, {"s-worker": session}, clear=False):
        resp = server._methods["config.set"](
            "rid", {"session_id": "s-worker", "key": "cwd", "value": str(cwd)}
        )
    assert resp["result"]["value"] == str(cwd)
    assert _read(worker)["terminal"]["cwd"] == str(cwd)
    assert os.environ["TERMINAL_CWD"] == "launch-cwd"
