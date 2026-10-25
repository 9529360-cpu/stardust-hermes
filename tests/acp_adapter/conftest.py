"""Shared fixtures for tests/acp_adapter.

Keeps the ACP server tests offline: ``HermesACPAgent._build_model_state``
calls ``hermes_cli.inventory.build_models_payload``, which (without this
fixture) performs live network fetches — models.dev registry, GitHub model
catalog, Copilot token exchange, Anthropic model list — adding ~3s of real
SSL/socket time to every test that creates or loads a session (~147s total
for test_server.py alone).

Tests that assert model-state behavior re-patch these same attributes with
``unittest.mock.patch`` / ``monkeypatch``; inner patches win, so this
default is transparent to them.
"""

import importlib.util

import pytest

# The ACP adapter depends on the optional ``acp`` extra
# (agent-client-protocol). Without it, importing these modules raises
# ModuleNotFoundError at collection time and hides real failures in a plain
# ``.[dev]`` environment. Ignore the directory before those imports run, and
# say so in the pytest header so the skip is visible.
_ACP_AVAILABLE = importlib.util.find_spec("acp") is not None
if not _ACP_AVAILABLE:
    collect_ignore_glob = ["test_*.py"]


def pytest_report_header(config):
    if _ACP_AVAILABLE:
        return None
    return "acp extra not installed: tests/acp_adapter skipped"


@pytest.fixture(autouse=True)
def _offline_model_inventory(monkeypatch):
    """Stub the shared model inventory so ACP tests never hit the network."""
    import hermes_cli.inventory as inventory

    class _StubPickerContext:
        def with_overrides(self, **_kwargs):
            return self

    monkeypatch.setattr(inventory, "load_picker_context", lambda: _StubPickerContext())
    monkeypatch.setattr(
        inventory,
        "build_models_payload",
        lambda *_args, **_kwargs: {"providers": []},
    )
