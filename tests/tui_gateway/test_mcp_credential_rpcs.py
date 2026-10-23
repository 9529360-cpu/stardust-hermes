"""CX3 tests: MCP API-key RPCs through the real registered gateway handlers.

Drives ``mcp.servers.add`` / ``mcp.servers.set_api_key`` against a real temp
``STARDUST_HOME`` (no mocks of the config/credential layer — same style as
test_mcp_profile_rpcs.py) and asserts the CX3 contract end to end:

- the credential lands in the CX2 encrypted store when the opt-in is enabled
  (config.yaml holds only a ``${connector:...}`` reference; ``.env`` untouched),
  else in the profile's ``.env`` with the ``${ENV}`` reference (legacy default)
- no RPC response, list/status read-back, or config write ever echoes the value
- URL/query/argv credential placement is rejected with nothing persisted
- a failed config save rolls the credential back (atomicity)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tui_gateway.server as server  # noqa: E402

CANARY = "CANARY-CX3-123"


def _call(method, params=None):
    handler = server._methods[method]
    return handler(1, params or {})


def _result(resp):
    assert "error" not in resp, resp.get("error")
    return resp["result"]


def _error(resp):
    assert "error" in resp, resp
    return resp["error"]


def _read_yaml(path: Path) -> dict:
    import yaml
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture()
def hermes_root(tmp_path, monkeypatch):
    root = tmp_path / "hermes_home"
    (root / "profiles" / "work").mkdir(parents=True)
    (root / "profiles" / "other").mkdir(parents=True)
    monkeypatch.setenv("STARDUST_HOME", str(root))
    return root


@pytest.fixture()
def encrypted_root(tmp_path, monkeypatch):
    """Launch profile with the CX2 opt-in on and a deterministic fake cipher."""
    root = tmp_path / "hermes_home"
    (root / "profiles" / "work").mkdir(parents=True)
    (root / "config.yaml").write_text("connector_secret_store:\n  enabled: true\n",
                                      encoding="utf-8")
    monkeypatch.setenv("STARDUST_HOME", str(root))

    from agent import connector_secrets as cs
    from agent import connector_secrets_dpapi as dpapi
    from agent.connector_secrets_dpapi import DpapiConnectorSecretStore

    def _xor(data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)

    monkeypatch.setattr(dpapi, "_dpapi_protect", _xor)
    monkeypatch.setattr(dpapi, "_dpapi_unprotect", _xor)
    monkeypatch.setattr(cs, "_build_platform_store", lambda: DpapiConnectorSecretStore())
    return root


# ---------------------------------------------------------------------------
# add + bearer_token / set_api_key — storage + reference + no echo
# ---------------------------------------------------------------------------

class TestAddWithCredential:
    def test_add_bearer_legacy_env_path(self, hermes_root):
        resp = _call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
            "bearer_token": CANARY,
        })
        result = _result(resp)
        assert result["credential_stored"] == "env"
        assert json.dumps(result).count(CANARY) == 0  # no value echo anywhere

        work = hermes_root / "profiles" / "work"
        entry = _read_yaml(work / "config.yaml")["mcp_servers"]["demo"]
        assert entry["headers"] == {"Authorization": "Bearer ${MCP_DEMO_API_KEY}"}
        assert CANARY in (work / ".env").read_text(encoding="utf-8")  # legacy slot, by design
        # Never in the launch profile.
        assert not (hermes_root / ".env").exists()

    def test_add_bearer_encrypted_path(self, encrypted_root):
        resp = _call("mcp.servers.add", {
            "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
            "bearer_token": CANARY,
        })
        result = _result(resp)
        assert result["credential_stored"] == "encrypted"
        assert json.dumps(result).count(CANARY) == 0

        entry = _read_yaml(encrypted_root / "config.yaml")["mcp_servers"]["demo"]
        assert entry["headers"] == {"Authorization": "Bearer ${connector:demo/api_key}"}
        assert not (encrypted_root / ".env").exists()
        blob = (encrypted_root / "connector-secrets" / "srv-demo" / "f-api_key.json").read_bytes()
        assert CANARY.encode("utf-8") not in blob

    def test_add_without_credential_unchanged_shape(self, hermes_root):
        result = _result(_call("mcp.servers.add", {
            "profile": "work", "name": "plain",
            "config": {"url": "https://mcp.example.com/api"},
        }))
        assert "credential_stored" not in result or result.get("credential_stored") is None
        assert not (hermes_root / "profiles" / "work" / ".env").exists()


class TestSetApiKey:
    def test_http_set_api_key_legacy(self, hermes_root):
        _call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
        })
        result = _result(_call("mcp.servers.set_api_key", {
            "profile": "work", "name": "demo", "value": CANARY,
        }))
        assert result["credential_stored"] == "env"
        assert result["env_var"] == "MCP_DEMO_API_KEY"
        assert json.dumps(result).count(CANARY) == 0
        work = hermes_root / "profiles" / "work"
        entry = _read_yaml(work / "config.yaml")["mcp_servers"]["demo"]
        assert entry["headers"] == {"Authorization": "Bearer ${MCP_DEMO_API_KEY}"}

    def test_stdio_set_api_key_encrypted(self, encrypted_root):
        _call("mcp.servers.add", {
            "name": "demo", "config": {"command": "demo-server"},
        })
        result = _result(_call("mcp.servers.set_api_key", {
            "name": "demo", "value": CANARY, "env_var": "DEMO_TOKEN",
        }))
        assert result["credential_stored"] == "encrypted"
        assert json.dumps(result).count(CANARY) == 0
        entry = _read_yaml(encrypted_root / "config.yaml")["mcp_servers"]["demo"]
        assert entry["env"]["DEMO_TOKEN"] == "${connector:demo/api_key}"
        assert not (encrypted_root / ".env").exists()

    def test_invalid_value_rejected_nothing_written(self, hermes_root):
        _call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
        })
        err = _error(_call("mcp.servers.set_api_key", {
            "profile": "work", "name": "demo", "value": "Bearer",
        }))
        assert "valid credential" in str(err)
        assert not (hermes_root / "profiles" / "work" / ".env").exists()


# ---------------------------------------------------------------------------
# R1 — placement rejection through the RPC surface
# ---------------------------------------------------------------------------

class TestPlacementRejection:
    def test_url_userinfo_rejected(self, hermes_root):
        err = _error(_call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://ak:sk@mcp.example.com/api"},
            "bearer_token": CANARY,
        }))
        assert "userinfo" in str(err)
        work = hermes_root / "profiles" / "work"
        assert "mcp_servers" not in _read_yaml(work / "config.yaml")
        assert not (work / ".env").exists()

    def test_url_query_token_rejected(self, hermes_root):
        err = _error(_call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api?token=abc"},
        }))
        assert "query parameter" in str(err)
        work = hermes_root / "profiles" / "work"
        assert "mcp_servers" not in _read_yaml(work / "config.yaml")

    def test_credential_in_args_rejected(self, hermes_root):
        err = _error(_call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"command": "prog", "args": [CANARY]},
            "bearer_token": CANARY,
        }))
        assert "command arguments" in str(err)
        assert not (hermes_root / "profiles" / "work" / ".env").exists()


# ---------------------------------------------------------------------------
# R3 — atomic rollback through the RPC surface
# ---------------------------------------------------------------------------

class TestRpcAtomicity:
    def test_config_failure_rolls_credential_back(self, hermes_root, monkeypatch):
        from hermes_cli import mcp_config as mc

        def _boom(cfg):
            raise OSError("disk full (test injection)")

        monkeypatch.setattr(mc, "save_config", _boom)
        err = _error(_call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
            "bearer_token": CANARY,
        }))
        assert "disk full" in str(err) or "failed to save" in str(err)
        work = hermes_root / "profiles" / "work"
        assert "mcp_servers" not in _read_yaml(work / "config.yaml")
        env_text = ((work / ".env").read_text(encoding="utf-8")
                    if (work / ".env").exists() else "")
        assert "MCP_DEMO_API_KEY" not in env_text
        assert CANARY not in env_text


# ---------------------------------------------------------------------------
# R2 — read-back surfaces never echo the value
# ---------------------------------------------------------------------------

class TestReadBackNeverEchoes:
    def test_list_and_status_carry_no_value(self, hermes_root):
        _call("mcp.servers.add", {
            "profile": "work", "name": "demo",
            "config": {"url": "https://mcp.example.com/api"},
            "bearer_token": CANARY,
        })
        listing = _result(_call("mcp.servers.list", {"profile": "work"}))
        assert json.dumps(listing).count(CANARY) == 0
        row = next(s for s in listing["servers"] if s["name"] == "demo")
        assert row["auth"] == "header"
        assert "headers" not in row
        status = _result(_call("mcp.servers.status", {"profile": "work"}))
        assert json.dumps(status).count(CANARY) == 0
