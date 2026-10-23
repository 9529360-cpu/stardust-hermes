"""Tests for the opt-in OS-encrypted connector secret store (CX2).

Covers the contract in agent/connector_secrets.py:

- opt-in off (the default): no store exists, ``${connector:...}`` placeholders
  stay literal, and legacy ``${ENV}`` interpolation is byte-for-byte unchanged
- saving FAILS CLOSED when the secure backend is unavailable — nothing is
  written to disk, never a plaintext fallback
- reference syntax (format/parse) and slugified hostile names
- DPAPI store logic under a fake cipher: round-trip, ciphertext (not the
  plaintext) on disk, restart via a fresh instance, missing entries, damaged
  blobs, backend/version guards, delete/list lifecycle, two profiles with the
  same server name, concurrent writers
- interpolation integration through tools.mcp_tool_config with the option on
- the generic config loader keeps ``${connector:...}`` verbatim (no "not
  resolvable" warning — the MCP loader owns that source)
- real Windows DPAPI round-trip incl. a separate process (``windows_only``)
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent.connector_secrets import (  # noqa: E402
    ConnectorSecretDecryptError,
    ConnectorSecretStoreUnavailable,
    connector_secret_reference,
    connector_secret_store_status,
    get_connector_secret_store,
    get_connector_secret_store_strict,
    parse_connector_reference,
    resolve_connector_secret_value,
)
from agent.connector_secrets_dpapi import DpapiConnectorSecretStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# XOR-with-constant "cipher": deterministic, and the plaintext bytes never
# appear in what hits the disk (that is the property under test).
_FAKE_KEY = 0x5A


def _fake_encrypt(data: bytes) -> bytes:
    return bytes(b ^ _FAKE_KEY for b in data)


@pytest.fixture()
def fake_dpapi(monkeypatch):
    """Stand in for the real crypt32 calls so the store logic is testable off Windows."""
    from agent import connector_secrets_dpapi as dpapi
    monkeypatch.setattr(dpapi, "_dpapi_protect", _fake_encrypt)
    monkeypatch.setattr(dpapi, "_dpapi_unprotect", _fake_encrypt)


def _write_config(home: Path, body: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(body, encoding="utf-8")


_ENABLED_CONFIG = "connector_secret_store:\n  enabled: true\n"


@pytest.fixture()
def enabled_home(tmp_path, monkeypatch):
    """Isolated profile home with the opt-in switch on."""
    home = tmp_path / "home"
    _write_config(home, _ENABLED_CONFIG)
    monkeypatch.setenv("STARDUST_HOME", str(home))
    return home


@pytest.fixture()
def disabled_home(tmp_path, monkeypatch):
    """Isolated profile home with no connector_secret_store stanza at all."""
    home = tmp_path / "home"
    _write_config(home, "model:\n  provider: test\n")
    monkeypatch.setenv("STARDUST_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# Reference syntax
# ---------------------------------------------------------------------------

class TestReferenceSyntax:
    def test_format_and_parse_roundtrip(self):
        ref = connector_secret_reference("github", "api_key")
        assert ref == "${connector:github/api_key}"
        assert parse_connector_reference("connector:github/api_key") == ("github", "api_key")

    @pytest.mark.parametrize("bad", [
        "", "connector:", "connector:/field", "connector:server/", "connector:a/b/c",
        "CONNECTOR:a/b", "env:connector:a/b", "github/api_key", "connector:server",
    ])
    def test_parse_rejects_malformed(self, bad):
        assert parse_connector_reference(bad) is None


# ---------------------------------------------------------------------------
# Opt-in off (default): legacy behavior untouched
# ---------------------------------------------------------------------------

class TestDisabledByDefault:
    def test_no_store_and_disabled_status(self, disabled_home):
        assert get_connector_secret_store() is None
        usable, reason = connector_secret_store_status()
        assert usable is False
        assert reason.startswith("disabled")

    def test_strict_raises_with_enable_hint(self, disabled_home):
        with pytest.raises(ConnectorSecretStoreUnavailable, match="connector_secret_store"):
            get_connector_secret_store_strict()

    def test_placeholder_stays_literal(self, disabled_home):
        from tools.mcp_tool_config import _interpolate_env_vars
        cfg = {"headers": {"Authorization": "Bearer ${connector:srv/api_key}"},
               "env": {"TOKEN": "${connector:srv/api_key}"}}
        assert _interpolate_env_vars(cfg) == cfg

    def test_legacy_env_interpolation_unchanged(self, disabled_home):
        from agent.secret_scope import reset_secret_scope, set_secret_scope
        from tools.mcp_tool_config import _interpolate_env_vars
        token = set_secret_scope({"MY_KEY": "legacy-value", "EMPTY_REF": ""})
        try:
            out = _interpolate_env_vars({
                "auth": "Bearer ${MY_KEY}",
                "missing": "x${NEVER_SET}y",
                "env_style": "${env:MY_KEY}",
            })
            assert out["auth"] == "Bearer legacy-value"
            assert out["missing"] == "x${NEVER_SET}y"
            assert out["env_style"] == "legacy-value"
        finally:
            reset_secret_scope(token)


# ---------------------------------------------------------------------------
# Fail-closed saving
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_unavailable_backend_writes_nothing(self, tmp_path, monkeypatch):
        from agent import connector_secrets_dpapi as dpapi

        def _refuse(data: bytes) -> bytes:
            raise ConnectorSecretStoreUnavailable("secure backend down (test)")

        monkeypatch.setattr(dpapi, "_dpapi_protect", _refuse)
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "connector-secrets")
        with pytest.raises(ConnectorSecretStoreUnavailable):
            store.store_secret("srv", "api_key", "secret-value")
        assert not (tmp_path / "connector-secrets").exists()

    def test_strict_owner_raises_when_backend_unavailable(self, enabled_home, monkeypatch):
        from agent import connector_secrets_dpapi as dpapi

        def _refuse(data: bytes) -> bytes:
            raise ConnectorSecretStoreUnavailable("secure backend down (test)")

        monkeypatch.setattr(dpapi, "_dpapi_protect", _refuse)
        usable, reason = connector_secret_store_status()
        assert usable is False
        assert "secure backend down" in reason
        with pytest.raises(ConnectorSecretStoreUnavailable):
            get_connector_secret_store_strict()

    def test_empty_value_rejected_without_touching_disk(self, tmp_path, fake_dpapi):
        from agent.connector_secrets import ConnectorSecretError
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "connector-secrets")
        with pytest.raises(ConnectorSecretError):
            store.store_secret("srv", "api_key", "")
        assert not (tmp_path / "connector-secrets").exists()


# ---------------------------------------------------------------------------
# Store logic under a fake cipher (runs on every host)
# ---------------------------------------------------------------------------

class TestStoreLogic:
    def test_roundtrip_not_plaintext_on_disk(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        store.store_secret("github", "api_key", "cx2-canary-value")
        # Fresh instance = the restart path: resolution only reads the file.
        assert DpapiConnectorSecretStore(base_dir=tmp_path / "secrets").resolve_secret(
            "github", "api_key") == "cx2-canary-value"
        raw = (tmp_path / "secrets" / "srv-github" / "f-api_key.json").read_bytes()
        assert b"cx2-canary-value" not in raw
        envelope = json.loads(raw.decode("utf-8"))
        assert envelope["version"] == 1
        assert envelope["backend"] == "windows-dpapi-currentuser"
        assert envelope["server"] == "github"
        assert envelope["field"] == "api_key"
        assert base64.b64decode(envelope["data"], validate=True) == _fake_encrypt(
            b"cx2-canary-value")

    def test_missing_entry_returns_none(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        store.store_secret("a", "f1", "v1")
        assert store.resolve_secret("a", "f2") is None
        assert store.resolve_secret("b", "f1") is None

    def test_damaged_ciphertext_raises_and_lenient_seam_returns_none(
            self, enabled_home, monkeypatch, fake_dpapi, caplog):
        from agent import connector_secrets_dpapi as dpapi

        # Refuse only real entries, not the availability probe: the scenario is
        # "backend works, this one ciphertext is damaged".
        probe_cipher = _fake_encrypt(b"stardust-connector-secret-store-probe")

        def _refuse_decrypt(blob: bytes) -> bytes:
            if blob == probe_cipher:
                return _fake_encrypt(blob)
            raise ConnectorSecretDecryptError(
                "Windows DPAPI could not decrypt this entry (test injection)")

        store = get_connector_secret_store_strict()
        store.store_secret("srv", "api_key", "stored-value")
        monkeypatch.setattr(dpapi, "_dpapi_unprotect", _refuse_decrypt)
        with pytest.raises(ConnectorSecretDecryptError):
            store.resolve_secret("srv", "api_key")
        with caplog.at_level("WARNING", logger="agent.connector_secrets"):
            assert resolve_connector_secret_value("srv", "api_key") is None
        assert any("could not be decrypted" in r.getMessage() for r in caplog.records)
        # The warning must carry the server/field, never the value.
        assert "stored-value" not in caplog.text

    def test_wrong_backend_tag_rejected(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        store.store_secret("srv", "api_key", "v")
        entry = tmp_path / "secrets" / "srv-srv" / "f-api_key.json"
        envelope = json.loads(entry.read_text(encoding="utf-8"))
        envelope["backend"] = "future-keychain"
        entry.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(ConnectorSecretDecryptError, match="future-keychain"):
            store.resolve_secret("srv", "api_key")

    def test_future_version_rejected(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        store.store_secret("srv", "api_key", "v")
        entry = tmp_path / "secrets" / "srv-srv" / "f-api_key.json"
        envelope = json.loads(entry.read_text(encoding="utf-8"))
        envelope["version"] = 2
        entry.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(ConnectorSecretDecryptError, match="version"):
            store.resolve_secret("srv", "api_key")

    def test_corrupt_json_is_replaced_by_rewrite(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        entry_dir = tmp_path / "secrets" / "srv-srv"
        entry_dir.mkdir(parents=True)
        (entry_dir / "f-api_key.json").write_text("{ not json", encoding="utf-8")
        store.store_secret("srv", "api_key", "fresh")
        assert store.resolve_secret("srv", "api_key") == "fresh"

    def test_delete_and_list_lifecycle(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        store.store_secret("srv", "api_key", "v1")
        store.store_secret("srv", "refresh_token", "v2")
        store.store_secret("other", "api_key", "v3")
        entries = {(e.server, e.field) for e in store.list_entries()}
        assert entries == {("srv", "api_key"), ("srv", "refresh_token"), ("other", "api_key")}
        assert "v1" not in repr(store.list_entries())
        assert store.delete_secret("srv", "api_key") is True
        assert store.delete_secret("srv", "api_key") is False
        assert store.resolve_secret("srv", "api_key") is None
        assert store.resolve_secret("srv", "refresh_token") == "v2"
        assert store.delete_server("srv") == 1
        assert store.resolve_secret("srv", "refresh_token") is None
        assert store.resolve_secret("other", "api_key") == "v3"
        assert store.delete_server("srv") == 0

    def test_two_profiles_same_server_name_isolated(self, tmp_path, fake_dpapi):
        home_a = tmp_path / "homeA" / "connector-secrets"
        home_b = tmp_path / "homeB" / "connector-secrets"
        DpapiConnectorSecretStore(base_dir=home_a).store_secret("github", "api_key", "profile-a")
        DpapiConnectorSecretStore(base_dir=home_b).store_secret("github", "api_key", "profile-b")
        assert DpapiConnectorSecretStore(base_dir=home_a).resolve_secret(
            "github", "api_key") == "profile-a"
        assert DpapiConnectorSecretStore(base_dir=home_b).resolve_secret(
            "github", "api_key") == "profile-b"

    @pytest.mark.parametrize("server,field", [
        ("../evil", "api_key"), ("a b", "c:d"), ("CON", "api_key"),
        ("сервер", "ключ"), ("srv", "CON"),
    ])
    def test_hostile_names_never_escape_base_dir(self, tmp_path, fake_dpapi, server, field):
        base = tmp_path / "secrets"
        store = DpapiConnectorSecretStore(base_dir=base)
        store.store_secret(server, field, "v")
        assert store.resolve_secret(server, field) == "v"
        for path in base.rglob("*"):
            rel = path.relative_to(base)
            if rel.parts[0] == ".lock":
                continue  # the store's own cross-process lock file
            assert rel.parts[0].startswith("srv-")
            assert ".." not in rel.parts
            # An unslugged server/field name must never appear as a whole path
            # component (traversal, reserved device names, raw unicode).
            for part in rel.parts:
                assert part != server and part != field

    @pytest.mark.parametrize("name", ["..", ".", "..."])
    def test_dot_only_names_rejected(self, tmp_path, fake_dpapi, name):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        from agent.connector_secrets import ConnectorSecretError
        with pytest.raises(ConnectorSecretError):
            store.store_secret(name, "api_key", "v")
        with pytest.raises(ConnectorSecretError):
            store.store_secret("srv", name, "v")
        assert not (tmp_path / "secrets").exists()

    def test_concurrent_writers_leave_valid_envelope(self, tmp_path, fake_dpapi):
        store = DpapiConnectorSecretStore(base_dir=tmp_path / "secrets")
        values = [f"writer-{i}" for i in range(4)]
        threads = [threading.Thread(target=store.store_secret, args=("srv", "api_key", v))
                   for v in values]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        resolved = store.resolve_secret("srv", "api_key")
        assert resolved in values
        envelope = json.loads((tmp_path / "secrets" / "srv-srv" / "f-api_key.json")
                              .read_text(encoding="utf-8"))
        assert envelope["version"] == 1


# ---------------------------------------------------------------------------
# Interpolation + config-loader integration with the option on
# ---------------------------------------------------------------------------

class TestResolutionIntegration:
    def test_interpolation_resolves_connector_refs_when_enabled(self, enabled_home, fake_dpapi):
        from tools.mcp_tool_config import _interpolate_env_vars
        get_connector_secret_store_strict().store_secret("github", "api_key", "cx2-value")
        out = _interpolate_env_vars({
            "headers": {"Authorization": "Bearer ${connector:github/api_key}"},
            "env": {"GITHUB_TOKEN": "${connector:github/api_key}"},
            "mixed": "${connector:github/api_key}-${connector:github/missing}",
            "plain": "no refs",
        })
        assert out["headers"]["Authorization"] == "Bearer cx2-value"
        assert out["env"]["GITHUB_TOKEN"] == "cx2-value"
        assert out["mixed"] == "cx2-value-${connector:github/missing}"
        assert out["plain"] == "no refs"

    def test_env_style_connector_ref_also_resolves(self, enabled_home, fake_dpapi):
        from tools.mcp_tool_config import _interpolate_env_vars
        get_connector_secret_store_strict().store_secret("s", "f", "v")
        assert _interpolate_env_vars("${env:connector:s/f}") == "v"

    def test_legacy_env_refs_still_work_when_enabled(self, enabled_home, fake_dpapi):
        from agent.secret_scope import reset_secret_scope, set_secret_scope
        from tools.mcp_tool_config import _interpolate_env_vars
        token = set_secret_scope({"MY_KEY": "still-legacy"})
        try:
            assert _interpolate_env_vars("Bearer ${MY_KEY}") == "Bearer still-legacy"
        finally:
            reset_secret_scope(token)

    def test_store_lives_under_profile_home(self, enabled_home, fake_dpapi):
        store = get_connector_secret_store_strict()
        store.store_secret("s", "f", "v")
        assert (enabled_home / "connector-secrets").is_dir()
        assert (enabled_home / "connector-secrets" / "srv-s" / "f-f.json").is_file()

    def test_config_loader_keeps_connector_refs_verbatim(self, tmp_path, monkeypatch, caplog):
        home = tmp_path / "cfg-home"
        _write_config(home, (
            _ENABLED_CONFIG
            + "mcp_servers:\n"
            + "  demo:\n"
            + "    url: \"https://example.com/${connector:srv/api_key}\"\n"
        ))
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from hermes_cli.config import load_config
        with caplog.at_level("WARNING"):
            servers = load_config().get("mcp_servers")
        assert servers["demo"]["url"] == "https://example.com/${connector:srv/api_key}"
        assert not any("not resolvable" in r.message for r in caplog.records)

    def test_config_loader_still_warns_for_unknown_sources(self, tmp_path, monkeypatch, caplog):
        home = tmp_path / "cfg-home"
        _write_config(home, "model:\n  provider: t\n")
        monkeypatch.setenv("STARDUST_HOME", str(home))
        from hermes_cli.config import _expand_env_vars
        with caplog.at_level("WARNING"):
            _expand_env_vars("${bitwarden:ITEM/field}")
        assert any("not resolvable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Real Windows DPAPI (native Windows only)
# ---------------------------------------------------------------------------

_RESTART_CHILD = (
    "import os, sys\n"
    "from agent.connector_secrets import get_connector_secret_store_strict\n"
    "value = get_connector_secret_store_strict().resolve_secret(sys.argv[1], sys.argv[2])\n"
    "print('RESOLVED' if value == os.environ['CX2_TEST_CANARY'] else 'MISMATCH')\n"
)


@pytest.mark.windows_only
class TestRealDpapi:
    def test_roundtrip_restart_and_ciphertext_on_disk(self, enabled_home):
        import secrets as _secrets
        canary = "cx2-" + _secrets.token_urlsafe(24)
        get_connector_secret_store_strict().store_secret("real-server", "api_key", canary)
        # New instance: decryption must come from the file on disk.
        assert get_connector_secret_store_strict().resolve_secret(
            "real-server", "api_key") == canary
        blob_files = list((enabled_home / "connector-secrets").rglob("f-*.json"))
        assert len(blob_files) == 1
        assert canary.encode("utf-8") not in blob_files[0].read_bytes()

    def test_separate_process_resolves(self, enabled_home):
        import secrets as _secrets
        canary = "cx2-" + _secrets.token_urlsafe(24)
        get_connector_secret_store_strict().store_secret("real-server", "api_key", canary)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PYTHONUTF8"] = "1"
        env["CX2_TEST_CANARY"] = canary
        result = subprocess.run(
            [sys.executable, "-B", "-c", _RESTART_CHILD, "real-server", "api_key"],
            capture_output=True, text=True, env=env, timeout=60, cwd=str(REPO_ROOT))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().endswith("RESOLVED")

    def test_damaged_real_blob_fails_closed(self, enabled_home):
        get_connector_secret_store_strict().store_secret("real-server", "api_key", "v")
        entry = next((enabled_home / "connector-secrets").rglob("f-*.json"))
        envelope = json.loads(entry.read_text(encoding="utf-8"))
        envelope["data"] = base64.b64encode(b"garbage-not-dpapi").decode("ascii")
        entry.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(ConnectorSecretDecryptError):
            get_connector_secret_store_strict().resolve_secret("real-server", "api_key")
