"""Regression coverage for classic CLI startup and Router disk cold starts."""

import json
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("config_exists", [True, False])
def test_classic_cli_initializes_config_watcher(tmp_path, monkeypatch, config_exists):
    from hermes_cli.cli_tui_mixin import CLITuiMixin
    from utils import file_signature

    config = tmp_path / "config.yaml"
    if config_exists:
        config.write_text("mcp_servers: {}\n", encoding="utf-8")
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: config)
    monkeypatch.setenv("STARDUST_DEFER_AGENT_STARTUP", "1")
    state = SimpleNamespace(config={})
    CLITuiMixin._tui_init_run_state(state)
    assert state._config_sig == (file_signature(config.stat()) if config_exists else None)
    assert state._pending_input.empty()
    assert state._should_exit is False


@pytest.mark.parametrize("timestamp,refresh", [("fresh", False), ("expired", True), ("invalid", True)])
def test_router_disk_cold_start_preserves_efforts(tmp_path, monkeypatch, timestamp, refresh):
    from providers import get_provider_profile

    profile = get_provider_profile("router")
    mod = sys.modules[type(profile).__module__]
    path = tmp_path / "router_catalog.json"
    stamp = {"fresh": time.time(), "expired": 1, "invalid": "broken"}[timestamp]
    path.write_text(json.dumps({"ts": stamp, "efforts": {"test-model": ["low", "high"]}}), encoding="utf-8")
    monkeypatch.setattr(mod, "_disk_path", lambda: path)
    monkeypatch.setattr(mod, "_efforts_cache", None)
    monkeypatch.setattr(mod, "_disk_checked", False)
    warmed = []
    monkeypatch.setattr(mod, "_warm_efforts_async", lambda: warmed.append(True))
    assert profile.supported_reasoning_efforts("test-model") == ("low", "high")
    assert bool(warmed) is refresh
    assert profile.supported_reasoning_efforts("test-model") == ("low", "high")
    assert len(warmed) == int(refresh)
