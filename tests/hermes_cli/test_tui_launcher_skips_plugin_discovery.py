
"""Regression test: the TUI launcher must not spend time on plugin discovery.

`hermes --tui` just spawns a Node process; the spawned tui_gateway backend
performs its own plugin discovery. Running discover_plugins() in the
launcher added ~0.5s to every `hermes --tui` startup for work the backend
then redoes. Plain chat must still discover plugins.
"""

from __future__ import annotations

from argparse import Namespace

from hermes_cli import main as main_mod
from hermes_cli import mcp_startup
from hermes_cli import plugins as plugins_mod


def _install_discover_spy(monkeypatch):
    calls = []

    def _discover():
        calls.append("discover")

    # Patch the real module's entry points. A two-name stand-in for the whole module broke
    # every other import from it: the MCP startup gate's has_enabled_agent_plugin_mcp import
    # failed, its fail-open path started real background MCP discovery, and that daemon
    # thread, still importing at interpreter exit, aborted the run on Linux ("FATAL: exception
    # not rethrown") after both tests had passed.
    monkeypatch.setattr(plugins_mod, "discover_plugins", _discover)
    # main.py now kicks discovery off in a background thread; both entry points count as
    # "discovery work happened in the launcher".
    monkeypatch.setattr(plugins_mod, "start_background_plugin_discovery", _discover)
    return calls


def _args(**overrides):
    base = {
        "accept_hooks": False,
        "yolo": False,
        "safe_mode": False,
        "command": None,
        "query": None,
        "image": None,
    }
    base.update(overrides)
    return Namespace(**base)


def test_plugin_discovery_skipped_for_tui_launch(monkeypatch):
    calls = _install_discover_spy(monkeypatch)
    main_mod._prepare_agent_startup(_args(tui=True))
    assert calls == [], (
        "Plugin discovery must not run in the TUI launcher: the spawned "
        "tui_gateway backend discovers plugins itself."
    )


def test_plugin_discovery_runs_for_plain_chat(monkeypatch):
    calls = _install_discover_spy(monkeypatch)
    main_mod._prepare_agent_startup(_args(tui=False, command="chat"))
    assert calls == ["discover"]
    # No MCP servers are configured here, so nothing may still be discovering in the background.
    assert not mcp_startup.mcp_discovery_in_flight()
