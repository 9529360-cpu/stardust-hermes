"""Regression: Desktop/Web must not expose the inherited Nous Portal account flow."""

from fastapi.testclient import TestClient

from hermes_cli import anon_auth
from hermes_cli.auth import _load_auth_store
from hermes_cli.web_server import _SESSION_TOKEN, app
from tests.hermes_cli.test_anon_upgrade import free_account, portal

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}

__all__ = ["free_account", "portal"]


def test_nous_desktop_oauth_route_is_retired_even_with_legacy_guest(portal, free_account):
    """A legacy guest identity must not resurrect the removed Desktop account/login product."""
    guest = anon_auth.ensure_portal_identity(explicit=True)
    before = _load_auth_store()

    listed = client.get("/api/providers/oauth", headers=HEADERS)
    assert listed.status_code == 200
    assert "nous" not in {row["id"] for row in listed.json()["providers"]}

    started = client.post("/api/providers/oauth/nous/start", headers=HEADERS)
    assert started.status_code == 400
    assert "Unknown provider nous" in started.text

    after = _load_auth_store()
    assert after == before
    assert after["providers"]["nous"]["anon_token"] == guest["anon_token"]
    assert portal.token_grants == 0


def test_nous_desktop_oauth_disconnect_is_retired(portal):
    anon_auth.ensure_portal_identity(explicit=True)
    before = _load_auth_store()

    response = client.delete("/api/providers/oauth/nous", headers=HEADERS)

    assert response.status_code == 400
    assert "Unknown provider: nous" in response.text
    assert _load_auth_store() == before
