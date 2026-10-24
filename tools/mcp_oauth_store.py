#!/usr/bin/env python3
"""Secure-storage codec for MCP OAuth tokens / client secrets (CX4).

``HermesTokenStorage`` (tools/mcp_oauth.py) persists OAuth state as plain JSON
under ``STARDUST_HOME/mcp-tokens/``: ``<name>.json`` carries the access /
refresh token and ``<name>.client.json`` the DCR client registration (which may
include ``client_secret``). This module moves those secret VALUES behind the
CX2 connector secret store (OS-user-bound ciphertext; Windows DPAPI today)
while the JSON files keep every non-secret field — expiry, scope, issuer
binding, redirect URIs — as a reference skeleton, so the on-disk files contain
zero plaintext token material.

Opt-in: ``connectors: {oauth_secure_store: true}`` in config.yaml (default
off). Off = this codec never engages and every byte of legacy behaviour is
unchanged. On = writes are fail-closed (a secret that cannot be encrypted is
never written to disk in any weaker form) and reads migrate a legacy plaintext
file once: values are copied into the secure store, then the file is atomically
rewritten as the reference skeleton.

Fields stored per server name (never printed, logged or echoed):

- ``oauth_access_token`` / ``oauth_refresh_token`` — token file
- ``oauth_client_secret`` — client registration file

The ``oauth_`` prefix keeps CX4 entries from aliasing CX3's ``api_key`` field
for the same server name. Profile isolation comes from the store's base dir
living under the active profile home (see agent/connector_secrets_dpapi.py).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Top-level marker written into a protected file: present = the secret fields
# are ``${connector:<server>/<field>}`` references, not values.
SECURE_STORE_MARKER = "secure_store"
_MARKER_VALUE = "connector"

FIELD_ACCESS_TOKEN = "oauth_access_token"
FIELD_REFRESH_TOKEN = "oauth_refresh_token"
FIELD_CLIENT_SECRET = "oauth_client_secret"

# (json key in the OAuth state file, store field name) per file kind.
TOKEN_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("access_token", FIELD_ACCESS_TOKEN),
    ("refresh_token", FIELD_REFRESH_TOKEN),
)
CLIENT_INFO_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("client_secret", FIELD_CLIENT_SECRET),
)


def oauth_secure_store_enabled() -> bool:
    """Read the ``connectors.oauth_secure_store`` opt-in. Any failure = off (fail safe)."""
    try:
        from hermes_cli.config import load_config_readonly
        section = load_config_readonly().get("connectors")
        return bool(isinstance(section, dict) and section.get("oauth_secure_store"))
    except Exception:
        return False


def get_oauth_secret_codec(server_name: str, hermes_home: Optional[str] = None) -> Optional["OAuthSecretCodec"]:
    """The codec for one MCP server's OAuth secrets, or ``None`` when the flag is off.

    Flag on with no usable OS backend still returns a codec: writes then fail
    closed with the backend's reason (never a plaintext fallback) and reads
    treat entries as missing so the flow re-authorizes — the flag must not
    silently degrade to plaintext on an unsupported platform.

    ``hermes_home`` is accepted for parity with ``HermesTokenStorage``'s
    constructor (the caller passes it through); the store itself resolves the
    active profile home contextually, so it is not used to relocate anything.
    """
    if not oauth_secure_store_enabled():
        return None
    from agent.connector_secrets import _build_platform_store
    return OAuthSecretCodec(server_name, _build_platform_store())


class OAuthSecretCodec:
    """Protect/resolve the secret fields of one server's OAuth state files."""

    def __init__(self, server_name: str, store: Any):
        self._server = str(server_name)
        self._store = store

    # -- low-level store access ------------------------------------------------

    def _store_value(self, field: str, value: str) -> None:
        self._store.store_secret(self._server, field, value)

    def _resolve_value(self, field: str) -> Optional[str]:
        try:
            return self._store.resolve_secret(self._server, field)
        except Exception as exc:  # DecryptError included: value-free message only
            logger.warning("MCP OAuth secret %s/%s could not be read from the secure "
                           "store (%s); treating it as absent", self._server, field, exc)
            return None

    def _delete_value(self, field: str) -> None:
        try:
            self._store.delete_secret(self._server, field)
        except Exception as exc:  # non-fatal — a stale ciphertext entry is inert
            logger.debug("Could not delete MCP OAuth secret %s/%s: %s", self._server, field, exc)

    def _is_reference(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith("${connector:")

    # -- protect (write path) --------------------------------------------------

    def _protect(self, data: Dict[str, Any], fields) -> Dict[str, Any]:
        """Move the secret values into the store; the file keeps references.

        Fail-closed: the first store failure raises before anything is
        persisted, so a secret that cannot be encrypted is never written in a
        weaker form. A field whose value is already a reference is skipped
        (idempotent re-protect of a skeleton).
        """
        out = dict(data)
        for json_key, field in fields:
            value = out.get(json_key)
            if value is None or value == "":
                self._delete_value(field)  # dropped from the model — clear stale ciphertext
                continue
            if self._is_reference(value):
                continue
            self._store_value(field, str(value))
            out[json_key] = self._reference(field)
        if fields:
            out[SECURE_STORE_MARKER] = _MARKER_VALUE
        return out

    def protect_tokens(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._protect(data, TOKEN_FIELDS)

    def protect_client_info(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._protect(data, CLIENT_INFO_FIELDS)

    def _reference(self, field: str) -> str:
        from agent.connector_secrets import connector_secret_reference
        return connector_secret_reference(self._server, field)

    # -- resolve (read path) ---------------------------------------------------

    def _resolve(self, data: Dict[str, Any], fields) -> Optional[Dict[str, Any]]:
        """Skeleton -> full values. ``None`` when any secret cannot be recovered."""
        out = dict(data)
        out.pop(SECURE_STORE_MARKER, None)
        for json_key, field in fields:
            ref_value = out.get(json_key)
            if ref_value is None:
                continue
            value = self._resolve_value(field)
            if value is None:
                return None
            out[json_key] = value
        return out

    def resolve_tokens(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._resolve(data, TOKEN_FIELDS)

    def resolve_client_info(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._resolve(data, CLIENT_INFO_FIELDS)

    # -- one-time legacy migration ----------------------------------------------

    def _migrate(self, data: Dict[str, Any], fields) -> Dict[str, Any]:
        """Legacy plaintext -> store + reference skeleton. Best effort: on store
        failure the original data is returned unchanged (the plaintext file is
        left as-is and retried on the next read) so a broken backend degrades
        to legacy reads, never to lost credentials."""
        if data.get(SECURE_STORE_MARKER) or not any(
                not self._is_reference(data.get(k)) and data.get(k) for k, _ in fields):
            return data
        try:
            skeleton = self._protect(data, fields)
        except Exception as exc:
            logger.warning("MCP OAuth secrets for '%s' could not be migrated to the "
                           "secure store (%s); keeping the existing file unchanged",
                           self._server, exc)
            return data
        return skeleton

    def migrate_tokens(self, data: Dict[str, Any], path: Path) -> Dict[str, Any]:
        """Token migration pins the absolute expiry BEFORE the rewrite: legacy files
        without ``expires_at`` derive remaining validity from the file mtime, and
        the rewrite would reset that clock, re-validating an expired token."""
        skeleton = self._migrate(data, TOKEN_FIELDS)
        if skeleton is not data and data.get("expires_in") is not None and data.get("expires_at") is None:
            try:
                mtime = path.stat().st_mtime
                data["expires_at"] = mtime + int(data["expires_in"])
                skeleton["expires_at"] = data["expires_at"]
            except (OSError, TypeError, ValueError):
                pass
        return skeleton

    def migrate_client_info(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._migrate(data, CLIENT_INFO_FIELDS)

    # -- lifecycle ---------------------------------------------------------------

    def delete_refresh_token(self) -> None:
        """Remove just the refresh-token entry (issuer-mismatch strip)."""
        self._delete_value(FIELD_REFRESH_TOKEN)

    def delete_client_secret(self) -> None:
        """Remove just the client-secret entry (DCR client poison)."""
        self._delete_value(FIELD_CLIENT_SECRET)

    def delete_oauth_fields(self) -> None:
        """Remove every CX4-owned entry for this server (token removal / poison)."""
        for _json_key, field in (*TOKEN_FIELDS, *CLIENT_INFO_FIELDS):
            self._delete_value(field)

    def capture(self) -> Dict[str, Optional[str]]:
        """Current store values (or None per field) for snapshot-based rollback."""
        return {field: self._resolve_value(field)
                for _json_key, field in (*TOKEN_FIELDS, *CLIENT_INFO_FIELDS)}

    def restore_capture(self, capture: Dict[str, Any]) -> None:
        """Roll the store back to a ``capture`` (None entries are deleted)."""
        for field, value in capture.items():
            if value is None:
                self._delete_value(str(field))
            else:
                try:
                    self._store_value(str(field), str(value))
                except Exception as exc:
                    logger.warning("MCP OAuth secret rollback for '%s' could not restore "
                                   "%s (%s)", self._server, field, exc)
