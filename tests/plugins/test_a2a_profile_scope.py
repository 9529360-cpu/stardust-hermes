from __future__ import annotations

import pytest

from agent import secret_scope as ss
from gateway.config import PlatformConfig
from plugins.platforms import a2a


@pytest.fixture(autouse=True)
def _restore_multiplex_state():
    ss.set_multiplex_active(False)
    yield
    ss.set_multiplex_active(False)


def test_standalone_port_env_enables_a2a(monkeypatch):
    monkeypatch.setenv("A2A_PORT", "9901")
    assert a2a.is_connected(PlatformConfig(enabled=True, extra={})) is True


def test_secondary_profile_does_not_borrow_default_a2a_port(monkeypatch):
    monkeypatch.setenv("A2A_PORT", "9901")
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope({})
    try:
        assert a2a.is_connected(PlatformConfig(enabled=True, extra={})) is False
    finally:
        ss.reset_secret_scope(token)


def test_secondary_profile_uses_its_own_a2a_port(monkeypatch):
    monkeypatch.delenv("A2A_PORT", raising=False)
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope({"A2A_PORT": "9902"})
    try:
        assert a2a.is_connected(PlatformConfig(enabled=True, extra={})) is True
    finally:
        ss.reset_secret_scope(token)


def test_explicit_platform_enablement_does_not_depend_on_env(monkeypatch):
    monkeypatch.delenv("A2A_PORT", raising=False)
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope({})
    try:
        assert a2a.is_connected(PlatformConfig(enabled=True, extra={"enabled": True})) is True
    finally:
        ss.reset_secret_scope(token)
