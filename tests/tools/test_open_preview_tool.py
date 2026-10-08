"""Tests for the GUI-surface ``open_preview`` tool."""

import json

import pytest

from tools import desktop_ui, open_preview_tool as op
from tools.preview_tool import PREVIEW_SCHEMA
from tools.drive_preview_tool import ACT_PREVIEW_SCHEMA


def test_desktop_browser_schema_prefers_live_visible_session():
    assert "FIRST choice" in PREVIEW_SCHEMA["description"]
    assert "same in-app Browser" in PREVIEW_SCHEMA["description"]
    assert "SAME live page" in ACT_PREVIEW_SCHEMA["description"]

from tools.registry import registry


@pytest.fixture(autouse=True)
def _reset_emitter():
    """Each test controls the emitter; never leak one across tests."""
    desktop_ui.set_emitter(None)
    yield
    desktop_ui.set_emitter(None)


def test_lives_in_the_gui_surface_toolset(monkeypatch):
    import tools.preview_tool  # noqa: F401 — registers desktop_preview
    """Consolidated (#95681): this module's tool became an action of the
    single `desktop_preview` tool in desktop_ui; the old registration is gone and
    `preview` reaches a desktop client on ANY backend (no env gate)."""
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    assert registry.get_entry("open_preview") is None
    entry = registry.get_entry("desktop_preview")
    assert entry is not None
    assert entry.toolset == "desktop_ui"
    assert entry.check_fn is None


def test_emitter_failure_is_reported():
    def _boom(*_a):
        raise RuntimeError("no window")

    desktop_ui.set_emitter(_boom)
    assert "no window" in json.loads(op.open_preview_tool("https://x.example"))["error"]
