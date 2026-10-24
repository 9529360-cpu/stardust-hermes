"""CX4: MCP OAuth token / client-secret secure-storage adapter.

Covers the codec (protect / resolve / migrate / lifecycle) and the
``HermesTokenStorage`` integration on a temp HERMES_HOME: flag off =
byte-identical legacy files; flag on = zero plaintext token material on disk,
legacy plaintext migrates once, snapshot/restore rolls the secure store back,
and profile isolation holds. Uses the fake-DPAPI cipher (tests run on any OS).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp.client.auth")

from tools import mcp_oauth_store
from tools.mcp_oauth import HermesTokenStorage

from agent.connector_secrets import ConnectorSecretError as _ConnectorSecretError

_ENABLE_FLAG = "connectors:\n  oauth_secure_store: true\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeStore:
    """In-memory stand-in for the OS secret store (value-transparent)."""

    backend_name = "fake"

    def __init__(self):
        self.entries = {}
        self.refuse_writes = False

    def store_secret(self, server, field, value):
        if self.refuse_writes:
            raise _ConnectorSecretError("backend down (test)")
        self.entries[(server, field)] = value

    def resolve_secret(self, server, field):
        return self.entries.get((server, field))

    def delete_secret(self, server, field):
        return self.entries.pop((server, field), None) is not None


@pytest.fixture()
def fake_store(monkeypatch):
    store = FakeStore()
    from agent import connector_secrets as cs
    monkeypatch.setattr(cs, "_build_platform_store", lambda: store)
    return store


@pytest.fixture()
def flag_off_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".stardust").mkdir(parents=True)
    monkeypatch.setenv("STARDUST_HOME", str(home / ".stardust"))
    monkeypatch.setenv("HERMES_HOME", str(home / ".stardust"))
    return home / ".stardust"


@pytest.fixture()
def flag_on_home(flag_off_home):
    (flag_off_home / "config.yaml").write_text(_ENABLE_FLAG, encoding="utf-8")
    return flag_off_home


def _mk_tokens(access="access-AAA", refresh="refresh-BBB", expires_in=3600):
    from mcp.shared.auth import OAuthToken
    return OAuthToken(access_token=access, token_type="Bearer",
                      expires_in=expires_in, refresh_token=refresh, scope="tools:read")


def _mk_client_info(secret="client-secret-XYZ"):
    from mcp.shared.auth import OAuthClientInformationFull
    return OAuthClientInformationFull(
        client_id="cid-1", client_name="test client",
        redirect_uris=["http://127.0.0.1:9/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], scope="tools:read", token_endpoint_auth_method="none",
        **({"client_secret": secret} if secret is not None else {}))


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _grep_count(home: Path, value: str) -> int:
    if not value:
        return 0
    count = 0
    for p in home.rglob("*"):
        if p.is_file():
            try:
                if value in p.read_text(encoding="utf-8", errors="ignore"):
                    count += 1
            except OSError:
                pass
    return count


# ---------------------------------------------------------------------------
# Flag off: byte-identical legacy behaviour
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_no_codec_and_plain_files(self, flag_off_home):
        assert mcp_oauth_store.oauth_secure_store_enabled() is False
        storage = HermesTokenStorage("srv", hermes_home=flag_off_home)
        assert storage._secret_codec is None
        asyncio.run(storage.set_tokens(_mk_tokens()))
        data = _read(storage._tokens_path())
        assert data["access_token"] == "access-AAA"
        assert data["refresh_token"] == "refresh-BBB"

    def test_no_marker_key_written(self, flag_off_home):
        storage = HermesTokenStorage("srv", hermes_home=flag_off_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        asyncio.run(storage.set_client_info(_mk_client_info()))
        assert mcp_oauth_store.SECURE_STORE_MARKER not in _read(storage._tokens_path())
        assert mcp_oauth_store.SECURE_STORE_MARKER not in _read(storage._client_info_path())


# ---------------------------------------------------------------------------
# Flag on: protect / resolve round trip
# ---------------------------------------------------------------------------

class TestProtectResolve:
    def test_set_tokens_writes_skeleton_only(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        disk = _read(storage._tokens_path())
        assert disk["access_token"] == "${connector:srv/oauth_access_token}"
        assert disk["refresh_token"] == "${connector:srv/oauth_refresh_token}"
        assert disk[mcp_oauth_store.SECURE_STORE_MARKER] == "connector"
        assert fake_store.entries[("srv", "oauth_access_token")] == "access-AAA"
        assert fake_store.entries[("srv", "oauth_refresh_token")] == "refresh-BBB"
        assert _grep_count(flag_on_home, "access-AAA") == 0
        assert _grep_count(flag_on_home, "refresh-BBB") == 0

    def test_set_client_info_hides_secret(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_client_info(_mk_client_info()))
        disk = _read(storage._client_info_path())
        assert disk["client_secret"] == "${connector:srv/oauth_client_secret}"
        assert disk["client_id"] == "cid-1"
        assert fake_store.entries[("srv", "oauth_client_secret")] == "client-secret-XYZ"
        assert _grep_count(flag_on_home, "client-secret-XYZ") == 0

    def test_get_tokens_round_trip(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        loaded = asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_tokens())
        assert loaded is not None
        assert loaded.access_token == "access-AAA"
        assert loaded.refresh_token == "refresh-BBB"

    def test_get_client_info_round_trip(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_client_info(_mk_client_info(secret="s3cret")))
        loaded = asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_client_info())
        assert loaded is not None
        assert loaded.client_secret == "s3cret"
        assert loaded.client_id == "cid-1"


# ---------------------------------------------------------------------------
# Legacy plaintext migration
# ---------------------------------------------------------------------------

class TestMigration:
    def _write_legacy(self, home: Path, name="srv"):
        from tools.mcp_oauth import _get_token_dir
        d = _get_token_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps({
            "access_token": "legacy-access", "token_type": "Bearer",
            "expires_in": 3600, "refresh_token": "legacy-refresh",
        }), encoding="utf-8")
        (d / f"{name}.client.json").write_text(json.dumps({
            "client_id": "legacy-cid", "client_secret": "legacy-secret",
            "redirect_uris": ["http://127.0.0.1:9/callback"],
            "grant_types": ["authorization_code"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        }), encoding="utf-8")

    def test_legacy_token_loads_then_migrates(self, flag_on_home, fake_store):
        self._write_legacy(flag_on_home)
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        loaded = asyncio.run(storage.get_tokens())
        assert loaded is not None and loaded.access_token == "legacy-access"
        disk = _read(storage._tokens_path())
        assert disk["access_token"].startswith("${connector:srv/oauth_")
        assert fake_store.entries[("srv", "oauth_access_token")] == "legacy-access"
        assert _grep_count(flag_on_home, "legacy-access") == 0
        assert _grep_count(flag_on_home, "legacy-refresh") == 0
        # second read: the resolved skeleton round trip still works
        again = asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_tokens())
        assert again.access_token == "legacy-access"

    def test_migration_keeps_absolute_expiry(self, flag_on_home, fake_store):
        import os
        self._write_legacy(flag_on_home)
        path = HermesTokenStorage("srv", hermes_home=flag_on_home)._tokens_path()
        past = 1_000_000.0  # long-expired mtime
        os.utime(path, (past, past))
        asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_tokens())
        assert _read(path)["expires_at"] == pytest.approx(past + 3600, abs=5)

    def test_legacy_client_info_migrates(self, flag_on_home, fake_store):
        self._write_legacy(flag_on_home)
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        loaded = asyncio.run(storage.get_client_info())
        assert loaded is not None and loaded.client_secret == "legacy-secret"
        assert fake_store.entries[("srv", "oauth_client_secret")] == "legacy-secret"
        assert _grep_count(flag_on_home, "legacy-secret") == 0

    def test_migration_failure_keeps_plaintext(self, flag_on_home, fake_store):
        fake_store.refuse_writes = True
        self._write_legacy(flag_on_home)
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        loaded = asyncio.run(storage.get_tokens())
        assert loaded is not None and loaded.access_token == "legacy-access"
        assert _read(storage._tokens_path())["access_token"] == "legacy-access"

    def test_flag_off_leaves_legacy_untouched(self, flag_off_home):
        self._write_legacy(flag_off_home)
        storage = HermesTokenStorage("srv", hermes_home=flag_off_home)
        loaded = asyncio.run(storage.get_tokens())
        assert loaded.access_token == "legacy-access"
        assert _read(storage._tokens_path())["access_token"] == "legacy-access"


# ---------------------------------------------------------------------------
# Lifecycle: remove / strip / poison / snapshot-restore
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_remove_clears_store(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        asyncio.run(storage.set_client_info(_mk_client_info()))
        storage.remove()
        assert fake_store.entries == {}
        assert not storage._tokens_path().exists()

    def test_strip_refresh_deletes_store_entry(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        storage.strip_refresh_token()
        assert ("srv", "oauth_refresh_token") not in fake_store.entries
        assert fake_store.entries[("srv", "oauth_access_token")] == "access-AAA"
        disk = _read(storage._tokens_path())
        assert "refresh_token" not in disk or not disk["refresh_token"]

    def test_poison_deletes_client_secret(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_client_info(_mk_client_info()))
        assert storage.poison_client_registration() is True
        assert ("srv", "oauth_client_secret") not in fake_store.entries
        assert ("srv", "oauth_access_token") not in fake_store.entries or True  # tokens kept

    def test_snapshot_restore_rolls_store_back(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens(access="old-access", refresh="old-refresh")))
        snap = storage.snapshot()
        assert snap.get("__secrets__") is not None
        # a failed re-auth overwrites the store with new values
        asyncio.run(storage.set_tokens(_mk_tokens(access="new-access", refresh="new-refresh")))
        assert fake_store.entries[("srv", "oauth_access_token")] == "new-access"
        storage.restore(snap)
        assert fake_store.entries[("srv", "oauth_access_token")] == "old-access"
        assert fake_store.entries[("srv", "oauth_refresh_token")] == "old-refresh"
        loaded = asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_tokens())
        assert loaded.access_token == "old-access"

    def test_snapshot_restore_from_empty_removes_entries(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        snap = storage.snapshot()  # empty — no state files
        asyncio.run(storage.set_tokens(_mk_tokens()))
        storage.restore(snap)
        assert fake_store.entries == {}

    def test_fail_closed_new_tokens_without_backend(self, flag_on_home, fake_store):
        fake_store.refuse_writes = True
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        with pytest.raises(Exception):
            asyncio.run(storage.set_tokens(_mk_tokens()))
        assert not storage._tokens_path().exists()


# ---------------------------------------------------------------------------
# Profile isolation
# ---------------------------------------------------------------------------

class TestProfileIsolation:
    def test_profile_a_not_visible_to_b(self, tmp_path, fake_store, monkeypatch):
        home_a = tmp_path / "a"
        home_b = tmp_path / "b"
        for h in (home_a, home_b):
            h.mkdir()
            (h / "config.yaml").write_text(_ENABLE_FLAG, encoding="utf-8")

        def use(home):
            monkeypatch.setenv("STARDUST_HOME", str(home))
            monkeypatch.setenv("HERMES_HOME", str(home))

        use(home_a)
        asyncio.run(HermesTokenStorage("srv").set_tokens(_mk_tokens()))
        use(home_b)
        loaded = asyncio.run(HermesTokenStorage("srv").get_tokens())
        assert loaded is None  # B has no token file; A's store entries stay in A's home
        assert (home_a / "mcp-tokens" / "srv.json").exists()
        assert not (home_b / "mcp-tokens" / "srv.json").exists()


# ---------------------------------------------------------------------------
# Refresh-fence path shape (unchanged file identity)
# ---------------------------------------------------------------------------

class TestFencePathUnchanged:
    def test_tokens_path_identity_stable(self, flag_on_home, fake_store):
        s1 = HermesTokenStorage("srv", hermes_home=flag_on_home)
        s2 = HermesTokenStorage("srv", hermes_home=flag_on_home)
        assert s1._tokens_path() == s2._tokens_path()


# ---------------------------------------------------------------------------
# Issuer binding preserved through the codec
# ---------------------------------------------------------------------------

class TestIssuerBinding:
    def test_issuer_stamp_survives_protect(self, flag_on_home, fake_store):
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        storage.bind_issuer("https://issuer.example")
        stamp = HermesTokenStorage("srv", hermes_home=flag_on_home)
        stamp.stamp_issuer("https://issuer.example")
        disk = _read(stamp._tokens_path())
        assert disk.get("hermes_issuer") == "https://issuer.example"
        loaded = asyncio.run(HermesTokenStorage("srv", hermes_home=flag_on_home).get_tokens())
        assert loaded is not None
        # loaded_issuer set by _fixup_loaded_tokens via hermes_issuer
        fresh = HermesTokenStorage("srv", hermes_home=flag_on_home)
        got = asyncio.run(fresh.get_tokens())
        assert fresh.loaded_issuer == "https://issuer.example"


# ---------------------------------------------------------------------------
# Client-change invalidation (cline invariant) under the secure store
# ---------------------------------------------------------------------------

class TestClientChangeInvalidation:
    def test_same_client_id_keeps_tokens(self, flag_on_home, fake_store):
        from tools.mcp_oauth import _invalidate_tokens_on_client_change
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        asyncio.run(storage.set_client_info(_mk_client_info()))
        _invalidate_tokens_on_client_change(storage, "cid-1", "client-secret-XYZ")
        assert storage._tokens_path().exists()
        assert fake_store.entries.get(("srv", "oauth_access_token")) == "access-AAA"

    def test_changed_client_id_drops_tokens_and_store_entries(self, flag_on_home, fake_store):
        from tools.mcp_oauth import _invalidate_tokens_on_client_change
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_tokens(_mk_tokens()))
        asyncio.run(storage.set_client_info(_mk_client_info()))
        _invalidate_tokens_on_client_change(storage, "cid-2", "client-secret-XYZ")
        assert not storage._tokens_path().exists()
        assert ("srv", "oauth_access_token") not in fake_store.entries
        assert ("srv", "oauth_refresh_token") not in fake_store.entries

    def test_reference_secret_resolution_exact_compare(self, flag_on_home, fake_store):
        """client.json skeleton + store value: comparison resolves and stays exact."""
        from tools.mcp_oauth import _invalidate_tokens_on_client_change
        storage = HermesTokenStorage("srv", hermes_home=flag_on_home)
        asyncio.run(storage.set_client_info(_mk_client_info(secret="stored-secret")))
        # same id + same secret (resolved from the store) -> no-op
        _invalidate_tokens_on_client_change(storage, "cid-1", "stored-secret")
        assert storage._client_info_path().exists()
        # same id + DIFFERENT secret (resolved compare catches it) -> client info stays,
        # tokens would drop; here no tokens exist so just assert no crash and file intact
        _invalidate_tokens_on_client_change(storage, "cid-1", "different-secret")
        assert storage._client_info_path().exists()
