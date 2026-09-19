from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest
import yaml

from agent import route_health
from agent.error_classifier import FailoverReason


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    route_health.reset_for_tests()
    return home


def _write_config(home: Path, data: dict) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _read_config(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}


def test_health_shows_configured_routes_and_persisted_block(isolated_home, capsys):
    _write_config(
        isolated_home,
        {
            "model": {"provider": "anthropic", "default": "claude-primary"},
            "fallback_providers": [
                {"provider": "openrouter", "model": "fallback-model"},
            ],
        },
    )
    route_health.record_failure(
        "openrouter",
        "fallback-model",
        "https://openrouter.ai/api/v1",
        FailoverReason.timeout,
    )

    from hermes_cli.fallback_cmd import cmd_fallback_health

    cmd_fallback_health(types.SimpleNamespace())
    output = capsys.readouterr().out

    assert "Persistent route health" in output
    assert "Primary:" in output
    assert "Fallback 1:" in output
    assert "BLOCKED" in output
    assert "reason=timeout" in output
    assert "endpoint=https://openrouter.ai/api/v1" in output
    assert "Inspection is read-only" in output


def test_health_command_does_not_claim_half_open_probe(isolated_home, capsys):
    _write_config(
        isolated_home,
        {
            "fallback_providers": [
                {"provider": "p", "model": "m", "base_url": "https://x.test"},
            ],
        },
    )
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    state = route_health.snapshot()
    row = next(iter(state["routes"].values()))
    row["cooldown_until"] = 0
    route_health._write_state(state)

    from hermes_cli.fallback_cmd import cmd_fallback_health

    cmd_fallback_health(types.SimpleNamespace())
    output = capsys.readouterr().out

    assert "PROBE READY" in output
    assert route_health.allow_route("p", "m", "https://x.test") == (
        True,
        0,
        "half_open_probe",
    )


def test_health_command_does_not_rewrite_persisted_state(isolated_home, capsys):
    _write_config(
        isolated_home,
        {
            "fallback_providers": [
                {"provider": "p", "model": "m", "base_url": "https://x.test"},
            ],
        },
    )
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    path = route_health.state_path()
    before = path.read_bytes()

    from hermes_cli.fallback_cmd import cmd_fallback_health

    cmd_fallback_health(types.SimpleNamespace())
    capsys.readouterr()

    assert path.read_bytes() == before


def test_corrupt_health_state_is_reported_and_repairable(isolated_home, capsys):
    _write_config(
        isolated_home,
        {
            "model": {"provider": "anthropic", "default": "primary"},
            "fallback_providers": [{"provider": "openrouter", "model": "fallback"}],
        },
    )
    path = route_health.state_path()
    path.write_text("not-json", encoding="utf-8")

    from hermes_cli.fallback_cmd import cmd_fallback_health, cmd_fallback_reset_health

    cmd_fallback_health(types.SimpleNamespace())
    health_output = capsys.readouterr().out
    assert "CORRUPT" in health_output
    assert "routing is failing open" in health_output
    assert "ELIGIBLE" not in health_output

    cmd_fallback_reset_health(types.SimpleNamespace(yes=True))
    reset_output = capsys.readouterr().out
    assert "Reset corrupt route-health state" in reset_output
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1, "routes": {}}


def test_reset_health_clears_state_but_preserves_fallback_chain(isolated_home, capsys):
    config = {
        "model": {"provider": "anthropic", "default": "primary"},
        "fallback_providers": [
            {"provider": "openrouter", "model": "fallback"},
        ],
    }
    _write_config(isolated_home, config)
    route_health.record_failure(
        "openrouter",
        "fallback",
        "https://openrouter.ai/api/v1",
        FailoverReason.rate_limit,
    )

    from hermes_cli.fallback_cmd import cmd_fallback_reset_health

    cmd_fallback_reset_health(types.SimpleNamespace(yes=True))
    output = capsys.readouterr().out

    assert route_health.snapshot()["routes"] == {}
    persisted = _read_config(isolated_home)
    assert persisted["fallback_providers"] == config["fallback_providers"]
    assert persisted["model"] == config["model"]
    assert "Cleared 1 entry" in output
    assert "next real request may probe" in output


def test_reset_health_cancel_keeps_state(isolated_home, capsys, monkeypatch):
    _write_config(isolated_home, {})
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "n")

    from hermes_cli.fallback_cmd import cmd_fallback_reset_health

    cmd_fallback_reset_health(types.SimpleNamespace(yes=False))
    output = capsys.readouterr().out

    assert route_health.snapshot()["routes"]
    assert "Cancelled" in output


def test_fallback_parser_wires_health_and_noninteractive_reset():
    from hermes_cli.subcommands.fallback import build_fallback_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_fallback_parser(subparsers)

    health = parser.parse_args(["fallback", "health"])
    reset = parser.parse_args(["fallback", "reset-health", "--yes"])

    assert health.fallback_command == "health"
    assert reset.fallback_command == "reset-health"
    assert reset.yes is True
