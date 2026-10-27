"""Per-profile adapter settings resolve explicit scoped env → the profile's own YAML → default.

Invariants from the #108440 post-merge review: a multiplexed secondary constructed under the real
``_profile_runtime_scope`` reads its own YAML lists/flags (Matrix authz lists, Telegram proxy,
Discord mentions), never inherits the launch process's env on a scoped miss, and the central
``allow_bots`` gate agrees with the adapter; an explicit env value still beats YAML for the owning
profile (single-profile contract). Real loader, real constructors; only transport is substituted.
"""

from __future__ import annotations

import asyncio
import contextlib
import types
from unittest.mock import AsyncMock, patch

import pytest

from agent.secret_scope import set_multiplex_active
from gateway.config import Platform, load_gateway_config
from hermes_cli.plugins import discover_plugins


@pytest.fixture(autouse=True)
def _plugins():
    discover_plugins()


class _Mentions:
    """``discord.AllowedMentions`` stand-in exposing the four flags (the suite stubs ``discord``)."""

    def __init__(self, *, everyone, roles, users, replied_user):
        self.everyone, self.roles, self.users, self.replied_user = everyone, roles, users, replied_user


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """(launch_home, secondary_home); STARDUST_HOME points at the launch home, multiplex off on exit."""
    launch = tmp_path / "launch"
    secondary = tmp_path / "launch" / "profiles" / "b2"
    secondary.mkdir(parents=True)
    monkeypatch.setenv("STARDUST_HOME", str(launch))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    yield launch, secondary
    set_multiplex_active(False)


@contextlib.contextmanager
def _secondary_scope(home):
    from gateway.run import _profile_runtime_scope
    set_multiplex_active(True)
    try:
        with _profile_runtime_scope(home, prepared_secret_scope={}):
            yield
    finally:
        set_multiplex_active(False)


def test_whatsapp_bridge_env_carries_the_secondary_effective_policy(homes, monkeypatch):
    """bridge.js gates DMs before Python: it must receive the adapter's resolved dm_policy/allow_from, not the
    launch process's WHATSAPP_* values."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    _, secondary = homes
    monkeypatch.setenv("WHATSAPP_DM_POLICY", "allowlist")
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15550001111")
    (secondary / "config.yaml").write_text("whatsapp:\n  enabled: true\n  dm_policy: pairing\n")
    with _secondary_scope(secondary):
        a = WhatsAppAdapter(load_gateway_config().platforms[Platform.WHATSAPP])
        env = a._bridge_env()
    assert a._dm_policy == "pairing" == env["WHATSAPP_DM_POLICY"]
    assert "WHATSAPP_ALLOWED_USERS" not in env
