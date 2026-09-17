"""Webhook egress under multiplex stays bound to the routed profile (#65939, #84266).

A ``/p/<profile>/`` route's reply, deliver_only message, home-channel fallback and
``github_comment`` credential all belong to THAT profile; a default-bound route never
borrows a secondary's adapter. Real ``GatewayAuthorizationMixin`` resolver, real
``load_gateway_config`` against a temp HERMES_HOME — no patched predicates.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.authz_mixin import GatewayAuthorizationMixin
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _api_request_profile
from gateway.platforms.base import SendResult
from gateway.platforms.webhook import WebhookAdapter


class _Runner(GatewayAuthorizationMixin):
    def __init__(self, adapters, profile_adapters, config=None):
        self.adapters = adapters
        self._profile_adapters = profile_adapters
        self._primary_profile_name = "default"
        self.config = config or GatewayConfig()


def _target():
    t = MagicMock()
    t.send = AsyncMock(return_value=SendResult(success=True))
    return t


def _webhook(runner) -> WebhookAdapter:
    adapter = WebhookAdapter(PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": {}}))
    adapter.gateway_runner = runner
    return adapter


@pytest.fixture
def profile_homes(tmp_path, monkeypatch):
    """Default home with a DEFAULT-HOME Slack home channel; ``profiles/sec`` with SEC-HOME + its own GH_TOKEN."""
    home = tmp_path / ".hermes"
    sec = home / "profiles" / "sec"
    sec.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.profiles._get_default_hermes_home", lambda: home)
    monkeypatch.setattr("hermes_cli.profiles._get_profiles_root", lambda: home / "profiles")
    (sec / "config.yaml").write_text(
        "gateway:\n  multiplex_profiles: true\nplatforms:\n  slack:\n    enabled: true\n"
        "    home_channel:\n      platform: slack\n      chat_id: SEC-HOME\n")
    (sec / ".env").write_text("GH_TOKEN=sec-token\n")
    monkeypatch.setenv("GH_TOKEN", "default-token")  # multiplex: os.environ == the default profile
    default_cfg = GatewayConfig()
    default_cfg.platforms[Platform.SLACK] = PlatformConfig(
        enabled=True, home_channel=HomeChannel(platform=Platform.SLACK, chat_id="DEFAULT-HOME", name="Home"))
    return default_cfg


@pytest.mark.asyncio
async def test_routed_profile_delivers_via_its_own_adapter_and_home_channel(profile_homes):
    default, secondary = _target(), _target()
    adapter = _webhook(_Runner({Platform.SLACK: default}, {"sec": {Platform.SLACK: secondary}}, profile_homes))

    result = await adapter._deliver_cross_platform(
        "slack", "hi", {"deliver": "slack", "deliver_extra": {}, "profile": "sec"})

    assert result.success
    default.send.assert_not_awaited()
    assert secondary.send.await_args.args[0] == "SEC-HOME"


@pytest.mark.asyncio
async def test_delivery_fails_closed_instead_of_crossing_profiles(profile_homes):
    """Neither direction may borrow: a secondary route without the platform must not use the default
    bot, and a default-bound route must not use a platform parked only on a secondary."""
    default, secondary = _target(), _target()

    sec_without_slack = _webhook(_Runner({Platform.SLACK: default}, {"sec": {}}, profile_homes))
    res = await sec_without_slack._deliver_cross_platform(
        "slack", "hi", {"deliver": "slack", "deliver_extra": {"chat_id": "C1"}, "profile": "sec"})
    assert not res.success and "not connected" in res.error
    default.send.assert_not_awaited()

    default_without_slack = _webhook(_Runner({}, {"sec": {Platform.SLACK: secondary}}, profile_homes))
    res = await default_without_slack._deliver_cross_platform(
        "slack", "hi", {"deliver": "slack", "deliver_extra": {"chat_id": "C1"}, "profile": None})
    assert not res.success and "not connected" in res.error
    secondary.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_comment_authenticates_with_routed_profile_token(profile_homes, monkeypatch):
    seen = {}
    sec = Path(os.environ["HERMES_HOME"]) / "profiles" / "sec"
    (sec / ".env").write_text("GH_TOKEN=sec-token\nGH_HOST=github.com\n")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "default-enterprise-token")
    monkeypatch.setenv("GITHUB_ENTERPRISE_TOKEN", "default-enterprise-token-2")
    monkeypatch.setenv("GH_HOST", "enterprise.default.example")
    monkeypatch.setenv("GH_REPO", "default/ambient")
    monkeypatch.setenv("GH_CONFIG_DIR", str(Path(os.environ["HERMES_HOME"]) / "default-gh-config"))

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or os.environ)
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr("gateway.platforms.webhook.subprocess.run", fake_run)
    adapter = _webhook(_Runner({}, {"sec": {}}, profile_homes))

    res = await adapter._deliver_github_comment(
        "body", {"deliver": "github_comment", "profile": "sec", "deliver_extra": {"repo": "o/r", "pr_number": "7"}})

    assert res.success
    assert seen["GH_TOKEN"] == "sec-token"
    assert seen["GH_HOST"] == "github.com"
    for key in ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_REPO", "GH_CONFIG_DIR"):
        assert key not in seen, (key, seen.get(key))


@pytest.mark.asyncio
async def test_github_comment_named_profile_without_token_fails_closed(profile_homes, monkeypatch):
    """A named profile must never fall through to the machine-wide ``gh`` login."""
    sec = Path(os.environ["HERMES_HOME"]) / "profiles" / "sec"
    (sec / ".env").write_text("")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr("gateway.platforms.webhook.subprocess.run", fake_run)
    adapter = _webhook(_Runner({}, {"sec": {}}, profile_homes))
    from agent import secret_scope as ss
    previous = ss.is_multiplex_active()
    ss.set_multiplex_active(True)
    try:
        res = await adapter._deliver_github_comment(
            "body", {"deliver": "github_comment", "profile": "sec", "deliver_extra": {"repo": "o/r", "pr_number": "7"}})
    finally:
        ss.set_multiplex_active(previous)

    assert not res.success
    assert "credential" in res.error.lower()
    assert called is False

@pytest.mark.asyncio
async def test_routed_profile_script_drops_launch_profile_runtime_residue(profile_homes, monkeypatch):
    """A routed profile's script gets its home, never the launch profile's runtime settings."""
    from agent import secret_scope as ss

    sec = Path(os.environ["HERMES_HOME"]) / "profiles" / "sec"
    scripts = sec / "scripts"
    scripts.mkdir()
    (scripts / "env_probe.py").write_text(
        "import json, os\n"
        "print(json.dumps({'home': os.getenv('HERMES_HOME'), 'model': os.getenv('HERMES_MODEL'), "
        "'terminal': os.getenv('TERMINAL_ENV')}))\n",
        encoding="utf-8",
    )
    root = Path(os.environ["HERMES_HOME"])
    (root / ".env").write_text("HERMES_MODEL=default-model\nTERMINAL_ENV=docker\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_MODEL", "default-model")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    adapter = _webhook(_Runner({}, {"sec": {}}, profile_homes))
    previous = ss.is_multiplex_active()
    ss.set_multiplex_active(True)
    try:
        with adapter._profile_scope("sec"):
            keep, transformed = adapter._route_processor.run_route_script("env_probe.py", {})
    finally:
        ss.set_multiplex_active(previous)

    assert keep is True
    assert transformed == {"home": str(sec), "model": None, "terminal": None}
def test_api_server_profile_callback_resolves_routed_profile_adapter_fail_closed():
    default, secondary = object(), object()
    api = APIServerAdapter(PlatformConfig(enabled=True, extra={"port": 0}))
    request = MagicMock()
    request.app = {}

    api.gateway_runner = _Runner({Platform("google_chat"): default}, {"sec": {Platform("google_chat"): secondary}})
    token = _api_request_profile.set("sec")
    try:
        assert api._get_platform_callback_adapter(request, "google_chat") is secondary
        api.gateway_runner = _Runner({Platform("google_chat"): default}, {"sec": {}})
        assert api._get_platform_callback_adapter(request, "google_chat") is None
    finally:
        _api_request_profile.reset(token)
    # No prefix: the primary map, unchanged.
    assert api._get_platform_callback_adapter(request, "google_chat") is default
