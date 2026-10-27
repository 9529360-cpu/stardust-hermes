"""Invariant: a standalone sender's failure envelope never carries vendor secrets to the model.

Standalone channel senders share ``gateway.platforms._shared.send_error`` so a token or signed URL in a
transport exception cannot reach the tool result. The integration case exercises a currently shipped
WhatsApp sender through a fake local bridge.
"""

import pytest

import agent.redact as _redact
from gateway.config import PlatformConfig
from gateway.platforms._shared import send_error
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_FAKE_TOKEN = "hermes-test-bearer-credential-ABCDEFGHIJKLMNOPQRSTUVWX"
_FAKE_URL_SECRET = "https://hooks.example/send?access_token=sk_live_ABCDEF0123456789"


@pytest.fixture(autouse=True)
def _redaction_on(monkeypatch):
    # The switch is snapshotted at import; a developer shell with STARDUST_REDACT_SECRETS=false must not
    # turn this contract test into a no-op.
    monkeypatch.setattr(_redact, "_REDACT_ENABLED", True)


def test_send_error_redacts_token_and_signed_url():
    out = send_error(f"upstream said 401 for Authorization: Bearer {_FAKE_TOKEN} at {_FAKE_URL_SECRET}")
    assert set(out) == {"error"}
    assert _FAKE_TOKEN not in out["error"]
    assert "sk_live_ABCDEF0123456789" not in out["error"]
    assert "401" in out["error"]  # the diagnostic shape survives


@pytest.mark.asyncio
async def test_whatsapp_standalone_failure_is_redacted(monkeypatch):
    import aiohttp

    whatsapp = load_plugin_adapter("whatsapp")

    class _BoomResponse:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return f"Authorization: Bearer {_FAKE_TOKEN} {_FAKE_URL_SECRET}"

    class _BoomSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _BoomResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    result = await whatsapp._standalone_send(
        PlatformConfig(enabled=True, extra={"bridge_port": 3000}), "12345", "hello"
    )

    assert "error" in result
    assert _FAKE_TOKEN not in result["error"]
    assert "sk_live_ABCDEF0123456789" not in result["error"]
    assert "bridge error" in result["error"]
