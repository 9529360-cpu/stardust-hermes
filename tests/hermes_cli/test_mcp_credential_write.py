"""CX3 tests: safe write & handling of MCP API keys (credential layer).

Covers the contract in hermes_cli/mcp_config.py:
- placement validation (R1): credentials in URL userinfo/query, argv, or a
  literal Authorization header are rejected with actionable errors
- storage selection (R2): CX2 encrypted store when enabled (config gets only a
  ``${connector:...}`` reference), legacy ``.env`` when disabled — and no path
  ever returns the value, only the masked ``storage`` state
- atomicity (R3): a failed config save rolls the credential back (new →
  removed, replace → previous value restored) and leaves config untouched; a
  failed credential save never touches config
- profile scoping (R4): the write lands in the active profile's home only
- legacy compatibility: pre-existing ``${ENV}`` servers are not rewritten
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hermes_cli import mcp_config as mc  # noqa: E402

CANARY = "CANARY-CX3-123"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _write_config(home: Path, body: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(body, encoding="utf-8")


@pytest.fixture()
def legacy_home(tmp_path, monkeypatch):
    """Profile home with the CX2 opt-in OFF (default): .env path expected."""
    home = tmp_path / "home"
    _write_config(home, "model:\n  provider: test\n")
    monkeypatch.setenv("STARDUST_HOME", str(home))
    return home


@pytest.fixture()
def encrypted_home(tmp_path, monkeypatch):
    """Profile home with the CX2 opt-in ON, backed by a deterministic fake cipher."""
    home = tmp_path / "home"
    _write_config(home, "connector_secret_store:\n  enabled: true\n")
    monkeypatch.setenv("STARDUST_HOME", str(home))

    from agent import connector_secrets as cs
    from agent import connector_secrets_dpapi as dpapi
    from agent.connector_secrets_dpapi import DpapiConnectorSecretStore

    def _xor(data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)

    monkeypatch.setattr(dpapi, "_dpapi_protect", _xor)
    monkeypatch.setattr(dpapi, "_dpapi_unprotect", _xor)
    monkeypatch.setattr(cs, "_build_platform_store", lambda: DpapiConnectorSecretStore())
    return home


def _read_yaml(path: Path) -> dict:
    import yaml
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# R1 — secret placement validation
# ---------------------------------------------------------------------------

class TestSecretPlacement:
    def _issues(self, cfg, secret=CANARY):
        return mc._mcp_secret_placement_issues("demo", cfg, secret_value=secret)

    def test_url_userinfo_rejected(self):
        issues = self._issues({"url": "https://user:secretpw@example.com/mcp"})
        assert any("userinfo" in i for i in issues)

    @pytest.mark.parametrize("query", ["?token=abc", "?api_key=abc", "?client_secret=abc",
                                       "?access_token=abc", "?password=abc"])
    def test_credential_shaped_query_params_rejected(self, query):
        issues = self._issues({"url": f"https://example.com/mcp{query}"})
        assert any("query parameter" in i for i in issues)

    def test_query_reference_and_benign_params_pass(self):
        assert self._issues({"url": "https://example.com/mcp?page=2&token=${TOK}"}) == []

    def test_credential_inside_url_rejected(self):
        issues = self._issues({"url": f"https://example.com/mcp#{CANARY}"})
        assert any("inside the server url" in i for i in issues)

    def test_credential_inside_args_rejected(self):
        issues = self._issues({"command": "prog", "args": ["--flag", f"{CANARY}"]})
        assert any("command arguments" in i for i in issues)

    def test_literal_authorization_header_rejected(self):
        issues = self._issues({"url": "https://example.com/mcp",
                               "headers": {"Authorization": f"Bearer {CANARY}"}})
        assert any("literal credential" in i for i in issues)

    def test_reference_templates_pass(self):
        assert self._issues({"url": "https://example.com/mcp",
                             "headers": {"Authorization": "Bearer ${MCP_DEMO_API_KEY}"}}) == []
        assert self._issues({"url": "https://example.com/mcp",
                             "headers": {"Authorization": "Bearer ${connector:demo/api_key}"}}) == []

    def test_web_normalize_rejects_secret_placement(self, legacy_home):
        from hermes_cli.web_models import MCPServerCreate
        from hermes_cli.web_server_mcp import _normalize_mcp_server_create
        with pytest.raises(ValueError, match="userinfo"):
            _normalize_mcp_server_create(MCPServerCreate(
                name="demo", url="https://ak:sk@example.com/mcp", auth="none"))
        with pytest.raises(ValueError, match="query parameter"):
            _normalize_mcp_server_create(MCPServerCreate(
                name="demo", url="https://example.com/mcp?token=abc", auth="none"))


# ---------------------------------------------------------------------------
# Storage selection + fragment shape (R2)
# ---------------------------------------------------------------------------

class TestStorageSelection:
    def test_legacy_env_path_when_disabled(self, legacy_home):
        storage, entry = mc._commit_mcp_server_credential(
            "demo", CANARY, base_config={"url": "https://example.com/mcp", "trust": "full"})
        assert storage == "env"
        env_text = (legacy_home / ".env").read_text(encoding="utf-8")
        assert "MCP_DEMO_API_KEY" in env_text  # legacy plaintext slot, by design
        entry_yaml = _read_yaml(legacy_home / "config.yaml")["mcp_servers"]["demo"]
        assert entry_yaml["headers"] == {"Authorization": "Bearer ${MCP_DEMO_API_KEY}"}
        assert json.dumps(entry_yaml).count(CANARY) == 0

    def test_encrypted_path_when_enabled(self, encrypted_home):
        storage, entry = mc._commit_mcp_server_credential(
            "demo", CANARY, base_config={"url": "https://example.com/mcp", "trust": "full"})
        assert storage == "encrypted"
        assert not (encrypted_home / ".env").exists()
        entry_yaml = _read_yaml(encrypted_home / "config.yaml")["mcp_servers"]["demo"]
        assert entry_yaml["headers"] == {"Authorization": "Bearer ${connector:demo/api_key}"}
        blob = (encrypted_home / "connector-secrets" / "srv-demo" / "f-api_key.json").read_bytes()
        assert CANARY.encode("utf-8") not in blob
        assert json.dumps(entry_yaml).count(CANARY) == 0

    def test_stdio_env_entry_shape(self, legacy_home):
        _storage, entry = mc._commit_mcp_server_credential(
            "demo", CANARY, base_config={"command": "demo-server", "trust": "full"})
        assert entry["env"]["MCP_DEMO_API_KEY"] == "${MCP_DEMO_API_KEY}"
        storage_yaml = _read_yaml(legacy_home / "config.yaml")["mcp_servers"]["demo"]
        assert storage_yaml["env"]["MCP_DEMO_API_KEY"] == "${MCP_DEMO_API_KEY}"

    def test_invalid_value_rejected_before_any_write(self, legacy_home):
        with pytest.raises(ValueError, match="valid credential"):
            mc._commit_mcp_server_credential("demo", "Bearer ", base_config={"url": "https://e.com"})
        assert not (legacy_home / ".env").exists()
        assert "mcp_servers" not in _read_yaml(legacy_home / "config.yaml")

    def test_bearer_prefix_stripped_once(self, legacy_home):
        _storage, _entry = mc._commit_mcp_server_credential(
            "demo", f"Bearer {CANARY}", base_config={"url": "https://example.com/mcp"})
        env_text = (legacy_home / ".env").read_text(encoding="utf-8")
        assert f"Bearer {CANARY}" not in env_text
        assert CANARY in env_text


# ---------------------------------------------------------------------------
# R3 — atomic rollback
# ---------------------------------------------------------------------------

class TestAtomicRollback:
    def _break_config_save(self, monkeypatch):
        def _boom(cfg):
            raise OSError("disk full (test injection)")
        monkeypatch.setattr(mc, "save_config", _boom)

    def test_new_credential_rolled_back_on_config_failure(self, legacy_home, monkeypatch):
        config_before = (legacy_home / "config.yaml").read_bytes()
        self._break_config_save(monkeypatch)
        with pytest.raises(OSError):
            mc._commit_mcp_server_credential(
                "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        assert (legacy_home / "config.yaml").read_bytes() == config_before
        env_text = ((legacy_home / ".env").read_text(encoding="utf-8")
                    if (legacy_home / ".env").exists() else "")
        assert "MCP_DEMO_API_KEY" not in env_text

    def test_replaced_credential_restored_on_config_failure(self, legacy_home, monkeypatch):
        mc._commit_mcp_server_credential(
            "demo", "OLD-KEY-1", base_config={"url": "https://example.com/mcp"})
        config_before = (legacy_home / "config.yaml").read_bytes()
        self._break_config_save(monkeypatch)
        with pytest.raises(OSError):
            mc._commit_mcp_server_credential(
                "demo", CANARY, existing={"url": "https://example.com/mcp"})
        assert (legacy_home / "config.yaml").read_bytes() == config_before
        env_text = (legacy_home / ".env").read_text(encoding="utf-8")
        assert "OLD-KEY-1" in env_text and CANARY not in env_text

    def test_encrypted_credential_rolled_back_on_config_failure(self, encrypted_home, monkeypatch):
        self._break_config_save(monkeypatch)
        with pytest.raises(OSError):
            mc._commit_mcp_server_credential(
                "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        assert not (encrypted_home / "connector-secrets" / "srv-demo" / "f-api_key.json").exists()

    def test_encrypted_replacement_restored_on_config_failure(self, encrypted_home, monkeypatch):
        mc._commit_mcp_server_credential(
            "demo", "OLD-KEY-1", base_config={"url": "https://example.com/mcp"})
        self._break_config_save(monkeypatch)
        with pytest.raises(OSError):
            mc._commit_mcp_server_credential(
                "demo", CANARY, existing={"url": "https://example.com/mcp"})
        store = mc._mcp_credential_storage()[1]
        assert store.resolve_secret("demo", "api_key") == "OLD-KEY-1"

    def test_failed_credential_save_never_touches_config(self, legacy_home, monkeypatch):
        config_before = (legacy_home / "config.yaml").read_bytes()

        def _boom(*_a, **_k):
            raise OSError("env write failed (test injection)")

        monkeypatch.setattr(mc, "save_env_value", _boom)
        with pytest.raises(OSError):
            mc._commit_mcp_server_credential(
                "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        assert (legacy_home / "config.yaml").read_bytes() == config_before

    def test_failed_encrypted_save_never_touches_config(self, encrypted_home, monkeypatch):
        from agent.connector_secrets import ConnectorSecretStoreUnavailable
        from agent.connector_secrets_dpapi import DpapiConnectorSecretStore
        config_before = (encrypted_home / "config.yaml").read_bytes()

        def _boom(*_a, **_k):
            raise ConnectorSecretStoreUnavailable("backend down (test injection)")

        # The owner hands out a fresh store instance per call, so patch the class.
        monkeypatch.setattr(DpapiConnectorSecretStore, "store_secret", _boom)
        with pytest.raises(ConnectorSecretStoreUnavailable):
            mc._commit_mcp_server_credential(
                "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        assert (encrypted_home / "config.yaml").read_bytes() == config_before


# ---------------------------------------------------------------------------
# R4 — profile scoping
# ---------------------------------------------------------------------------

class TestProfileScoping:
    def test_write_lands_only_in_active_profile(self, tmp_path, monkeypatch):
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        work, other = tmp_path / "work", tmp_path / "other"
        _write_config(work, "model:\n  provider: test\n")
        _write_config(other, "model:\n  provider: test\n")
        token = set_hermes_home_override(str(work))
        try:
            mc._commit_mcp_server_credential(
                "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        finally:
            reset_hermes_home_override(token)
        assert "MCP_DEMO_API_KEY" in (work / ".env").read_text(encoding="utf-8")
        assert not (other / ".env").exists()
        assert "mcp_servers" not in _read_yaml(other / "config.yaml")

    def test_encrypted_store_is_per_profile(self, encrypted_home, tmp_path, monkeypatch):
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        second = tmp_path / "second"
        _write_config(second, "connector_secret_store:\n  enabled: true\n")
        mc._commit_mcp_server_credential(
            "demo", CANARY, base_config={"url": "https://example.com/mcp"})
        token = set_hermes_home_override(str(second))
        try:
            storage, _entry = mc._commit_mcp_server_credential(
                "demo", "OTHER-PROFILE-KEY", base_config={"url": "https://example.com/mcp"})
        finally:
            reset_hermes_home_override(token)
        assert storage == "encrypted"
        assert not (second / "connector-secrets" / "srv-demo").exists() or \
            (second / "connector-secrets" / "srv-demo" / "f-api_key.json").read_bytes() != \
            (encrypted_home / "connector-secrets" / "srv-demo" / "f-api_key.json").read_bytes()


# ---------------------------------------------------------------------------
# Legacy compatibility — no batch rewrites
# ---------------------------------------------------------------------------

class TestLegacyCompatibility:
    def test_existing_env_servers_not_rewritten(self, legacy_home):
        _write_config(legacy_home, """\
model:
  provider: test
mcp_servers:
  legacy-srv:
    url: "https://old.example.com/mcp"
    headers:
      Authorization: "Bearer ${LEGACY_TOKEN}"
""")
        mc._commit_mcp_server_credential(
            "demo", CANARY, base_config={"url": "https://example.com/mcp", "trust": "full"})
        servers = _read_yaml(legacy_home / "config.yaml")["mcp_servers"]
        assert servers["legacy-srv"]["headers"]["Authorization"] == "Bearer ${LEGACY_TOKEN}"
        assert servers["legacy-srv"]["url"] == "https://old.example.com/mcp"

    def test_cli_bearer_helper_uses_encrypted_store_when_enabled(self, encrypted_home):
        headers = mc._save_bearer_auth_token("demo", CANARY)
        assert headers == {"Authorization": "Bearer ${connector:demo/api_key}"}
        assert not (encrypted_home / ".env").exists()

    def test_cli_bearer_helper_legacy_when_disabled(self, legacy_home):
        headers = mc._save_bearer_auth_token("demo", CANARY)
        assert headers == {"Authorization": "Bearer ${MCP_DEMO_API_KEY}"}
        assert CANARY in (legacy_home / ".env").read_text(encoding="utf-8")
