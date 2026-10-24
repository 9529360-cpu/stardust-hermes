"""CX2b: opt-in legacy MCP credential migration (encrypted store, default OFF).

Contract under test:
- flag OFF (default): no migration, no store files, no backup, no marker —
  config.yaml and .env stay byte-identical.
- flag ON: one-time migration moves ${VAR} credential slots into the store,
  rewrites references to ${connector:<server>/api_key}, backs up the
  plaintext files first, writes the marker; second run is a no-op.
- decrypt failure after migration falls back to the retained legacy copy
  (backup .env, then live .env, then the CX3 MCP_<SERVER>_API_KEY convention).
- unrelated .env keys and non-credential ${VAR} slots are never touched.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.connector_secrets import (  # noqa: E402
    resolve_connector_secret_value,
)
from agent.connector_secrets_migration import (  # noqa: E402
    _ATTEMPTED_HOMES,
    legacy_value_for_reference,
    maybe_migrate_connector_secrets,
    migrate_connector_secrets_once,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _fake_encrypt(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


@pytest.fixture()
def fake_store_owner(monkeypatch):
    """Pin the owner chain to the DPAPI store under the XOR fake cipher on every host."""
    from agent import connector_secrets as cs
    from agent import connector_secrets_dpapi as dpapi
    from agent.connector_secrets_dpapi import DpapiConnectorSecretStore
    monkeypatch.setattr(dpapi, "_dpapi_protect", _fake_encrypt)
    monkeypatch.setattr(dpapi, "_dpapi_unprotect", _fake_encrypt)
    monkeypatch.setattr(cs, "_build_platform_store", lambda: DpapiConnectorSecretStore())


def _setup_home(home: Path, config_body: str, env_body: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(config_body, encoding="utf-8")
    (home / ".env").write_text(env_body, encoding="utf-8")
    return home


def _config_with_servers(servers_yaml: str, enabled: bool) -> str:
    stanza = "connector_secret_store:\n  enabled: true\n" if enabled else "model:\n  provider: t\n"
    return stanza + "mcp_servers:\n" + servers_yaml


_SERVERS = (
    "  demo:\n"
    "    command: demo.exe\n"
    "    env:\n"
    "      DEMO_API_KEY: ${DEMO_API_KEY}\n"
    "  other:\n"
    "    command: other.exe\n"
    "    env:\n"
    "      PATH_HINT: ${PATH_HINT}\n"
)

_ENV = (
    "DEMO_API_KEY=secret-canary-value\n"
    "PATH_HINT=/usr/bin\n"
    "MODEL_PROVIDER_KEY=never-touch\n"
)


@pytest.fixture(autouse=True)
def _fresh_attempt_cache():
    _ATTEMPTED_HOMES.clear()
    yield
    _ATTEMPTED_HOMES.clear()


# --------------------------------------------------------------------------- #
# flag OFF (default): byte-identical legacy behaviour
# --------------------------------------------------------------------------- #

class TestFlagOffNoMigration:
    def test_no_files_created_and_no_rewrite(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=False), _ENV)
        before_config = (home / "config.yaml").read_bytes()
        before_env = (home / ".env").read_bytes()
        monkeypatch.setenv("STARDUST_HOME", str(home))

        assert migrate_connector_secrets_once(home) is False

        assert (home / "config.yaml").read_bytes() == before_config
        assert (home / ".env").read_bytes() == before_env
        assert not (home / "connector-secrets").exists()

    def test_hook_is_a_noop_off_main_wiring(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=False), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from tools.mcp_tool_config import _load_mcp_config
        servers = _load_mcp_config()
        # legacy resolution still works
        assert servers["demo"]["env"]["DEMO_API_KEY"] == "secret-canary-value"
        assert not (home / "connector-secrets").exists()


# --------------------------------------------------------------------------- #
# flag ON: migration, backup, marker, idempotence
# --------------------------------------------------------------------------- #

class TestFlagOnMigration:
    def test_migrates_rewrites_backs_up_and_marks(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))

        assert migrate_connector_secrets_once(home) is True

        # store entry created; ciphertext not plaintext
        entry = home / "connector-secrets" / "srv-demo" / "f-api_key.json"
        assert entry.is_file()
        assert b"secret-canary-value" not in entry.read_bytes()

        # config rewritten to the connector reference; unrelated env-block slots
        # follow the same credential-slot rule (documented CX3 write shape)
        from hermes_cli.config import read_raw_config
        raw = read_raw_config()
        assert raw["mcp_servers"]["demo"]["env"]["DEMO_API_KEY"] == "${connector:demo/api_key}"
        assert raw["mcp_servers"]["other"]["env"]["PATH_HINT"] == "${PATH_HINT}"

        # .env key removed, unrelated keys kept
        env_text = (home / ".env").read_text(encoding="utf-8")
        assert "DEMO_API_KEY" not in env_text
        assert "MODEL_PROVIDER_KEY=never-touch" in env_text
        assert "PATH_HINT=/usr/bin" in env_text

        # marker + backup with the original plaintext bytes
        marker = json.loads((home / "connector-secrets" / "migration.json").read_text())
        assert marker["migrated"] == {
            "demo/api_key": "DEMO_API_KEY"}
        backups = list((home / "connector-secrets").glob("backup-*"))
        assert len(backups) == 1
        assert b"DEMO_API_KEY=secret-canary-value" in (backups[0] / ".env").read_bytes()
        assert (backups[0] / "config.yaml").is_file()

        # the reference resolves through the store
        assert resolve_connector_secret_value("demo", "api_key") == "secret-canary-value"

    def test_second_run_is_noop_no_mtime_change(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True

        def snapshot() -> dict:
            return {
                str(p.relative_to(home)): (p.stat().st_mtime_ns, p.read_bytes())
                for p in sorted(home.rglob("*")) if p.is_file()
            }

        before = snapshot()
        assert migrate_connector_secrets_once(home) is False
        assert snapshot() == before

    def test_hook_wiring_migrates_on_config_load(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from tools.mcp_tool_config import _load_mcp_config
        servers = _load_mcp_config()
        assert servers["demo"]["env"]["DEMO_API_KEY"] == "secret-canary-value"
        assert (home / "connector-secrets" / "migration.json").is_file()
        # interpolation fell back to the store for the rewritten reference
        assert servers["other"]["env"]["PATH_HINT"] == "/usr/bin"

    def test_authorization_header_shape_preserved(self, tmp_path, monkeypatch, fake_store_owner):
        servers = (
            "  hdr:\n"
            "    command: h.exe\n"
            "    headers:\n"
            "      Authorization: Bearer ${HDR_TOKEN}\n"
        )
        home = _setup_home(
            tmp_path / "home", _config_with_servers(servers, enabled=True),
            "HDR_TOKEN=tok-123\n")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        from hermes_cli.config import read_raw_config
        raw = read_raw_config()
        assert raw["mcp_servers"]["hdr"]["headers"]["Authorization"] == \
            "Bearer ${connector:hdr/api_key}"

    def test_multiple_credential_refs_skip_server_entirely(self, tmp_path, monkeypatch,
                                                           fake_store_owner):
        NL = chr(10)
        servers = NL.join([
            "  multi:",
            "    command: m.exe",
            "    env:",
            "      KEY_A: ${KEY_A}",
            "      KEY_B: ${KEY_B}",
        ])
        env_body = NL.join(["KEY_A=aaa", "KEY_B=bbb", ""])
        home = _setup_home(tmp_path / "home", _config_with_servers(servers, enabled=True),
                           env_body)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        from hermes_cli.config import read_raw_config
        env = read_raw_config()["mcp_servers"]["multi"]["env"]
        # ambiguous ownership: everything stays legacy plaintext
        assert env["KEY_A"] == "${KEY_A}" and env["KEY_B"] == "${KEY_B}"
        assert "KEY_A=aaa" in (home / ".env").read_text()
        assert not (home / "connector-secrets" / "srv-multi").exists()

    def test_shared_noncredential_reference_stays_legacy(self, tmp_path, monkeypatch,
                                                        fake_store_owner):
        servers = "  demo:\n    command: demo\n    args: [\"${DEMO_API_KEY}\"]\n    env:\n      DEMO_API_KEY: ${DEMO_API_KEY}\n"
        home = _setup_home(tmp_path / "home", _config_with_servers(servers, True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        from hermes_cli.config import read_raw_config
        assert read_raw_config()["mcp_servers"]["demo"]["env"]["DEMO_API_KEY"] == "${DEMO_API_KEY}"
        assert "DEMO_API_KEY=" in (home / ".env").read_text()

    def test_dotenv_inline_comments_preserve_resolved_value(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, True),
                           'DEMO_API_KEY="quoted-canary" # annotation\nPATH_HINT=/bin\n')
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        assert resolve_connector_secret_value("demo", "api_key") == "quoted-canary"

    def test_shared_with_skipped_multi_server_keeps_legacy_key(self, tmp_path, monkeypatch, fake_store_owner):
        servers = ("  demo:\n    command: demo\n    env:\n      DEMO_API_KEY: ${DEMO_API_KEY}\n"
                   "  multi:\n    command: demo\n    env:\n      DEMO_API_KEY: ${DEMO_API_KEY}\n      OTHER_TOKEN: ${OTHER_TOKEN}\n")
        home = _setup_home(tmp_path / "home", _config_with_servers(servers, True),
                           _ENV + "OTHER_TOKEN=other-canary\n")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        assert "DEMO_API_KEY=" in (home / ".env").read_text()

    def test_no_credentials_writes_empty_marker(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", "connector_secret_store:\n  enabled: true\n", "")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        marker = json.loads((home / "connector-secrets" / "migration.json").read_text())
        assert marker["migrated"] == {}
        assert not list((home / "connector-secrets").glob("backup-*"))


# --------------------------------------------------------------------------- #
# decrypt failure fallback
# --------------------------------------------------------------------------- #

class TestDecryptFailureFallback:
    def _migrated_home(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        return home

    @staticmethod
    def _corrupt_entry(home: Path) -> None:
        entry = home / "connector-secrets" / "srv-demo" / "f-api_key.json"
        envelope = json.loads(entry.read_text(encoding="utf-8"))
        envelope["data"] = "!!!not-base64!!!"
        entry.write_text(json.dumps(envelope), encoding="utf-8")

    def test_falls_back_to_backup_value_with_value_free_warning(
            self, tmp_path, monkeypatch, fake_store_owner, caplog):
        from agent.connector_secrets import ConnectorSecretDecryptError

        home = self._migrated_home(tmp_path, monkeypatch, fake_store_owner)
        self._corrupt_entry(home)
        with caplog.at_level("WARNING", logger="agent.connector_secrets"):
            value = resolve_connector_secret_value("demo", "api_key")
        assert value == "secret-canary-value"
        assert any("legacy copy" in r.getMessage() for r in caplog.records)
        assert "secret-canary-value" not in caplog.text

    def test_no_legacy_value_returns_none(self, tmp_path, monkeypatch, fake_store_owner):
        home = self._migrated_home(tmp_path, monkeypatch, fake_store_owner)
        self._corrupt_entry(home)
        # wipe every legacy copy: backups and live .env
        for backup in (home / "connector-secrets").glob("backup-*"):
            (backup / ".env").unlink()
        (home / ".env").unlink()
        assert resolve_connector_secret_value("demo", "api_key") is None

    def test_backend_unavailable_uses_backup(self, tmp_path, monkeypatch, fake_store_owner):
        home = self._migrated_home(tmp_path, monkeypatch, fake_store_owner)
        from agent import connector_secrets as cs
        monkeypatch.setattr(cs, "_build_platform_store",
                            lambda: cs._NoSecureBackendStore("unavailable"))
        assert resolve_connector_secret_value("demo", "api_key") == "secret-canary-value"

    def test_no_backup_disables_only_migrated_server(self, tmp_path, monkeypatch, fake_store_owner):
        home = self._migrated_home(tmp_path, monkeypatch, fake_store_owner)
        self._corrupt_entry(home)
        for backup in (home / "connector-secrets").glob("backup-*"):
            (backup / ".env").unlink()
        from tools.mcp_tool_config import _load_mcp_config
        servers = _load_mcp_config()
        assert servers["demo"]["enabled"] is False
        assert servers["demo"]["disabled_reason"] == "connector_secret_unavailable"
        assert "other" in servers

    def test_cx3_env_key_convention_fallback(self, tmp_path, monkeypatch, fake_store_owner):
        # CX3 writer path: secret saved via the store directly (no migration
        # marker), legacy .env still holds the CX3 convention key
        home = _setup_home(tmp_path / "home",
                           _config_with_servers(_SERVERS, enabled=True),
                           "MCP_DEMO_API_KEY=convention-value\n")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from agent.connector_secrets import get_connector_secret_store_strict
        get_connector_secret_store_strict().store_secret("demo", "api_key", "stored-value")
        self._corrupt_entry(home)
        assert resolve_connector_secret_value("demo", "api_key") == "convention-value"

    def test_mcp_config_load_survives_corrupt_entry(self, tmp_path, monkeypatch, fake_store_owner):
        home = self._migrated_home(tmp_path, monkeypatch, fake_store_owner)
        self._corrupt_entry(home)
        from tools.mcp_tool_config import _load_mcp_config
        servers = _load_mcp_config()
        # fallback resolved the legacy value, so the server still gets its key
        assert servers["demo"]["env"]["DEMO_API_KEY"] == "secret-canary-value"
        assert servers["other"]["env"]["PATH_HINT"] == "/usr/bin"


# --------------------------------------------------------------------------- #
# no-backend platform (non-Windows semantics) and management seams
# --------------------------------------------------------------------------- #

class TestNoBackendAndSeams:
    def test_no_backend_store_migrates_nothing(self, tmp_path, monkeypatch):
        from agent import connector_secrets as cs
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        # pin the dispatcher to the platform store a non-Windows host would get
        monkeypatch.setattr(
            cs, "_build_platform_store",
            lambda: cs._NoSecureBackendStore("no backend on this platform (test)"))
        assert migrate_connector_secrets_once(home) is False
        assert not (home / "connector-secrets").exists()

    def test_managed_mode_skips_migration(self, tmp_path, monkeypatch, fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        monkeypatch.setattr("hermes_cli.config.is_managed", lambda: True)
        assert migrate_connector_secrets_once(home) is False
        assert not (home / "connector-secrets").exists()

    def test_hook_attempt_cache_prevents_repeat_probes(self, tmp_path, monkeypatch,
                                                       fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=False), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        maybe_migrate_connector_secrets()
        assert len(_ATTEMPTED_HOMES) == 1
        maybe_migrate_connector_secrets()
        assert len(_ATTEMPTED_HOMES) == 1

    def test_legacy_value_lookup_order_backup_then_live(self, tmp_path, monkeypatch,
                                                        fake_store_owner):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        assert legacy_value_for_reference("demo", "api_key") == "secret-canary-value"


@pytest.mark.windows_only
class TestOAuthMigrationSafety:
    def test_oauth_backup_and_decrypt_fallback(self, tmp_path, monkeypatch):
        home = _setup_home(tmp_path / "home",
                           "connector_secret_store:\n  enabled: true\nconnectors:\n  oauth_secure_store: true\n", "")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from tools.mcp_oauth import HermesTokenStorage
        storage = HermesTokenStorage("oauth-demo")
        path = storage._tokens_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps({"access_token": "oauth-canary", "token_type": "Bearer"}).encode()
        path.write_bytes(original)
        assert storage._read_state(path, "tokens")["access_token"] == "oauth-canary"
        backups = list((home / "connector-secrets").glob("backup-*/mcp-tokens/oauth-demo.json"))
        assert len(backups) == 1 and backups[0].read_bytes() == original
        entry = home / "connector-secrets/srv-oauth-demo/f-oauth_access_token.json"
        envelope = json.loads(entry.read_text())
        envelope["data"] = "garbage"
        entry.write_text(json.dumps(envelope))
        assert storage._read_state(path, "tokens")["access_token"] == "oauth-canary"


@pytest.mark.windows_only
class TestRealDpapiMigration:
    def test_migration_and_fallback_on_real_dpapi(self, tmp_path, monkeypatch):
        home = _setup_home(tmp_path / "home", _config_with_servers(_SERVERS, enabled=True), _ENV)
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert migrate_connector_secrets_once(home) is True
        entry = home / "connector-secrets" / "srv-demo" / "f-api_key.json"
        assert b"secret-canary-value" not in entry.read_bytes()
        assert resolve_connector_secret_value("demo", "api_key") == "secret-canary-value"

        envelope = json.loads(entry.read_text(encoding="utf-8"))
        envelope["data"] = "garbage"
        entry.write_text(json.dumps(envelope), encoding="utf-8")
        assert resolve_connector_secret_value("demo", "api_key") == "secret-canary-value"
