"""Inbound-port platforms of a SECONDARY profile are served on the default profile's shared listener
at ``/p/<profile>/<path>`` (gateway/platforms/shared_ingress.py).

Invariants: the forwarded request is verified by the NAMED profile's adapter with that profile's
secret and runs under that profile's runtime scope; the un-prefixed path is untouched; a profile
without an adapter for the path is a 404 rather than the default's adapter; a shared-listener
adapter binds no port of its own.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from gateway.config import GatewayConfig, Platform, PlatformConfig  # noqa: E402


def _cloud_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _cloud_body() -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "messages": [{"id": "probe", "type": "text", "text": {"body": "probe"}}],
        }}]}],
    }).encode()


class _Runner:
    """Just enough GatewayRunner for shared_ingress: the served-profile adapter map."""

    def __init__(self, adapters: dict[str, dict[Platform, Any]]):
        self.config = GatewayConfig(multiplex_profiles=True)
        self._profile_adapters = adapters
        self.adapters: dict = {}


def _cloud_adapter(secret: str, profile: str):
    from gateway.platforms.whatsapp_cloud import WhatsAppCloudAdapter
    adapter = WhatsAppCloudAdapter(PlatformConfig(enabled=True, extra={
        "phone_number_id": f"phone-{profile}", "access_token": f"tok-{profile}",
        "app_secret": secret, "verify_token": f"verify-{profile}", "webhook_port": 1,
    }))
    adapter._shared_listener_profile = profile
    adapter.set_owner_profile(profile)
    return adapter


async def _publish_cloud(adapter, runner) -> list[tuple[str, Path]]:
    """Wire the real Cloud handlers without Graph API access or a dedicated port.

    Stop at message ingestion, after the adapter has verified and decoded the request,
    and record the runtime home and secret scope used by the forwarded handler.
    """
    from agent.secret_scope import get_secret
    from gateway.platforms.shared_ingress import bind_listener
    from hermes_constants import get_hermes_home
    seen: list[tuple[str, Path]] = []
    adapter.gateway_runner = runner

    async def ingest(message, contacts, metadata):
        assert get_secret("WHATSAPP_CLOUD_APP_SECRET") == f"secret-{adapter._shared_listener_profile}"
        seen.append((message["text"]["body"], Path(get_hermes_home())))

    adapter._ingest_message = ingest
    app = web.Application(client_max_size=1024)
    app.router.add_get(adapter._webhook_path, adapter._handle_verify)
    app.router.add_post(adapter._webhook_path, adapter._handle_webhook)
    bound = await bind_listener(adapter, app, "127.0.0.1", 1, adapter._webhook_path)
    assert bound is None  # shared-listener mode never binds
    return seen


@pytest.fixture
def mux_home(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    for name in ("coder", "ops"):
        (root / "profiles" / name).mkdir(parents=True)
        (root / "profiles" / name / ".env").write_text(f"WHATSAPP_CLOUD_APP_SECRET=secret-{name}\n")
    monkeypatch.setenv("STARDUST_HOME", str(root))
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda multiplex: [("default", root), ("coder", root / "profiles" / "coder"), ("ops", root / "profiles" / "ops")])
    return root


async def _shared_listener(runner) -> TestClient:
    """The default profile's listener as the webhook adapter builds it: bare routes + /p/ forwarding."""
    from gateway.platforms.webhook import WebhookAdapter
    listener = WebhookAdapter(PlatformConfig(enabled=True, extra={"port": 1, "routes": {}}))
    listener.gateway_runner = runner
    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", listener._handle_webhook)
    app.router.add_post("/p/{profile}/webhooks/{route_name}", listener._handle_webhook)
    app.router.add_route("*", "/p/{profile}/{tail:.*}", listener._handle_profile_ingress)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_prefixed_cloud_webhook_is_verified_by_the_named_profiles_secret_under_its_scope(mux_home):
    coder, ops = _cloud_adapter("secret-coder", "coder"), _cloud_adapter("secret-ops", "ops")
    runner = _Runner({"coder": {Platform.WHATSAPP_CLOUD: coder}, "ops": {Platform.WHATSAPP_CLOUD: ops}})
    coder_seen, ops_seen = await _publish_cloud(coder, runner), await _publish_cloud(ops, runner)
    body = _cloud_body()
    async with await _shared_listener(runner) as client:
        for profile, secret in (("coder", "secret-coder"), ("ops", "secret-ops")):
            url = f"/p/{profile}/whatsapp/webhook"
            ok = await client.post(url, data=body, headers={"X-Hub-Signature-256": _cloud_sig(body, secret)})
            assert ok.status == 200
            # Another profile's secret and unsigned/tampered bodies must not reach ingestion.
            wrong_secret = "secret-ops" if profile == "coder" else "secret-coder"
            wrong = await client.post(url, data=body,
                                      headers={"X-Hub-Signature-256": _cloud_sig(body, wrong_secret)})
            assert wrong.status == 401
            assert (await client.post(url, data=body)).status == 401
            tampered = await client.post(url, data=body + b" ",
                                         headers={"X-Hub-Signature-256": _cloud_sig(body, secret)})
            assert tampered.status == 401
        # The un-prefixed path keeps serving only the default profile's own routes.
        bare = await client.post("/whatsapp/webhook", data=body,
                                 headers={"X-Hub-Signature-256": _cloud_sig(body, "secret-coder")})
        assert bare.status == 404
        # Unknown/default profiles and a profile without that platform never borrow another adapter.
        assert (await client.post("/p/nope/whatsapp/webhook", data=body)).status == 404
        assert (await client.post("/p/default/whatsapp/webhook", data=body)).status == 404
        runner._profile_adapters["ops"] = {}
        assert (await client.post("/p/ops/whatsapp/webhook", data=body,
                                  headers={"X-Hub-Signature-256": _cloud_sig(body, "secret-ops")})).status == 404
    assert coder_seen == [("probe", mux_home / "profiles" / "coder")]
    assert ops_seen == [("probe", mux_home / "profiles" / "ops")]


@pytest.mark.asyncio
async def test_shared_listener_adapter_records_its_public_ingress_url(mux_home, monkeypatch):
    """Status reports the prefixed URL; Meta's verification query reaches that same profile."""
    writes: list[dict] = []
    monkeypatch.setattr("gateway.status.write_runtime_status", lambda **kw: writes.append(kw))
    coder = _cloud_adapter("secret-coder", "coder")
    coder._runtime_status_platform_key = "coder:whatsapp_cloud"
    runner = _Runner({"coder": {Platform.WHATSAPP_CLOUD: coder}})
    runner.adapters = {Platform.API_SERVER: type("L", (), {"_host": "0.0.0.0", "_port": 8642})()}
    await _publish_cloud(coder, runner)
    assert coder._shared_ingress_url == "http://127.0.0.1:8642/p/coder/whatsapp/webhook"
    assert {"platform": "coder:whatsapp_cloud", "ingress_url": coder._shared_ingress_url} == {
        k: v for k, v in writes[-1].items() if k in ("platform", "ingress_url")}
    async with await _shared_listener(runner) as client:
        params = {"hub.mode": "subscribe", "hub.verify_token": "verify-coder", "hub.challenge": "profile challenge"}
        response = await client.get("/p/coder/whatsapp/webhook", params=params)
        assert response.status == 200
        assert await response.text() == "profile challenge"
        params["hub.verify_token"] = "verify-ops"
        assert (await client.get("/p/coder/whatsapp/webhook", params=params)).status == 403


@pytest.mark.asyncio
async def test_shared_listener_without_app_secret_fails_closed(mux_home, monkeypatch):
    """A profile without its own app secret cannot authenticate using the default process env."""
    monkeypatch.setenv("WHATSAPP_CLOUD_APP_SECRET", "default-process-secret")
    coder = _cloud_adapter("", "coder")
    runner = _Runner({"coder": {Platform.WHATSAPP_CLOUD: coder}})
    seen = await _publish_cloud(coder, runner)
    body = _cloud_body()
    async with await _shared_listener(runner) as client:
        response = await client.post("/p/coder/whatsapp/webhook", data=body,
                                     headers={"X-Hub-Signature-256": _cloud_sig(body, "default-process-secret")})
        assert response.status == 503
    assert seen == []
