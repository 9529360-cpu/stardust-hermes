"""OS-encrypted credential storage owner for connector/MCP secrets.

First step (CX2) of moving connector credentials (API keys, bearer tokens, later
OAuth material) off plaintext files — profile ``.env`` and ``mcp-tokens/*.json``
stay byte-identical plaintext — into an OS-user-bound encrypted store.

Opt-in and migration-safe: the store activates ONLY when
``connector_secret_store.enabled: true`` is set in config.yaml. With the option
off (the default) nothing changes — legacy plaintext credentials load exactly as
before, ``${connector:...}`` placeholders stay literal, no migration runs, and
no store file is ever created. The first config load under an enabled store
migrates legacy plaintext MCP credentials into it once
(agent/connector_secrets_migration.py), with a plaintext backup under
``<profile>/connector-secrets/backup-<ts>/`` and per-reference fallback while a
legacy copy survives.

The management seam later surfaces (CX3 key writer, CX4 OAuth adapter, CX7
disconnect cleanup) build on:

- ``get_connector_secret_store_strict()`` — for WRITERS: returns the active
  store or raises :class:`ConnectorSecretStoreUnavailable` with the reason.
  Saving a new secret FAILS CLOSED when the OS secure backend is unavailable;
  there is deliberately no plaintext/basic-text fallback.
- ``get_connector_secret_store()`` — for READERS: the active store, or ``None``
  when the option is off / the platform has no secure backend (legacy configs
  on such hosts keep working; only *new* secrets are refused).
- ``resolve_connector_secret_value(server, field)`` — lenient resolution used
  by config interpolation: ``None`` (placeholder kept) when the option is off,
  the entry is missing, or the ciphertext cannot be decrypted. Failures log a
  safe, value-free warning.

Backends bind a ciphertext to (OS user, profile home, server, field): the
Windows DPAPI backend stores ``<home>/connector-secrets/<server>/<field>.json``
envelopes whose ``data`` is a DPAPI CurrentUser blob, so two profiles running
the same server name never alias, and a blob copied to another user/machine
simply fails to decrypt. macOS Keychain / Linux Secret Service implement the
same interface in later PRs; until then those platforms report "no secure
backend" and the store refuses new secrets instead of downgrading.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("agent.connector_secrets")

# config.yaml root of the opt-in. Deliberately absent from DEFAULT_CONFIG
# (listed in hermes_cli/config.py _EXTRA_KNOWN_ROOT_KEYS): omitted unless used.
CONFIG_ROOT = "connector_secret_store"

# Placeholder body prefix: ``${connector:<server>/<field>}`` inside config.yaml
# (stdio env values, headers, URLs...). Resolved by tools.mcp_tool_config at
# MCP-config load time, NOT by the generic config env expansion — that layer
# only recognizes the prefix so it keeps the placeholder verbatim.
CONNECTOR_REF_PREFIX = "connector:"


class ConnectorSecretError(RuntimeError):
    """Connector secret store failure whose message is safe to surface (never carries a value)."""


class ConnectorSecretStoreUnavailable(ConnectorSecretError):
    """No usable OS secure backend for NEW secrets (fail closed — never a plaintext fallback)."""


class ConnectorSecretDecryptError(ConnectorSecretError):
    """Stored ciphertext could not be decrypted (wrong OS user, corrupted entry, stale backup)."""


class ConnectorSecretEntry:
    """Metadata-only view of one stored secret (no value, ever)."""

    __slots__ = ("server", "field", "created_at", "updated_at")

    def __init__(self, server: str, field: str, created_at: str, updated_at: str):
        self.server = server
        self.field = field
        self.created_at = created_at
        self.updated_at = updated_at

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return (f"ConnectorSecretEntry(server={self.server!r}, field={self.field!r}, "
                f"created_at={self.created_at!r}, updated_at={self.updated_at!r})")


class ConnectorSecretStore:
    """Interface every OS secure backend implements. Values only transit encrypted."""

    backend_name = "abstract"

    def is_available(self) -> Tuple[bool, str]:
        """(available, safe reason) — a live backend probe, not a platform check."""
        return False, f"{self.backend_name} does not implement is_available()"

    def store_secret(self, server: str, field: str, value: str) -> None:
        """Encrypt and persist. Must fail closed (raise) when the backend is unavailable."""
        raise ConnectorSecretError(f"{self.backend_name} does not implement store_secret()")

    def resolve_secret(self, server: str, field: str) -> Optional[str]:
        """Decrypt at use time; ``None`` when no entry exists. Decrypt failures raise
        :class:`ConnectorSecretDecryptError` so writers can verify a commit."""
        raise ConnectorSecretError(f"{self.backend_name} does not implement resolve_secret()")

    def delete_secret(self, server: str, field: str) -> bool:
        """Remove one entry; True when it existed."""
        raise ConnectorSecretError(f"{self.backend_name} does not implement delete_secret()")

    def delete_server(self, server: str) -> int:
        """Remove every entry owned by one connector (disconnect cleanup); count removed."""
        raise ConnectorSecretError(f"{self.backend_name} does not implement delete_server()")

    def list_entries(self) -> List[ConnectorSecretEntry]:
        """Metadata-only listing (server, field, timestamps — never values)."""
        raise ConnectorSecretError(f"{self.backend_name} does not implement list_entries()")


class _NoSecureBackendStore(ConnectorSecretStore):
    """Platform without a secure-storage backend: new secrets are refused, not downgraded."""

    backend_name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    def is_available(self) -> Tuple[bool, str]:
        return False, self._reason

    def store_secret(self, server: str, field: str, value: str) -> None:
        raise ConnectorSecretStoreUnavailable(self._reason)

    def resolve_secret(self, server: str, field: str) -> Optional[str]:
        return None

    def delete_secret(self, server: str, field: str) -> bool:
        return False

    def delete_server(self, server: str) -> int:
        return 0

    def list_entries(self) -> List[ConnectorSecretEntry]:
        return []


def connector_secret_reference(server: str, field: str) -> str:
    """The config.yaml placeholder writers put next to a stored secret."""
    return "${" + CONNECTOR_REF_PREFIX + server + "/" + field + "}"


def parse_connector_reference(name: str) -> Optional[Tuple[str, str]]:
    """``(server, field)`` from a ``connector:<server>/<field>`` name, else ``None``.

    Structural validation only (single ``/``, both sides non-empty); character
    policy is the storage backend's job (it slugifies for the filesystem).
    """
    if not name.startswith(CONNECTOR_REF_PREFIX):
        return None
    body = name[len(CONNECTOR_REF_PREFIX):]
    server, sep, field = body.partition("/")
    if not sep or not server or not field or "/" in field:
        return None
    return server, field


def _connector_store_enabled() -> bool:
    """Read the opt-in from config.yaml. Any read failure counts as off (fail safe)."""
    try:
        from hermes_cli.config import load_config_readonly
        section = load_config_readonly().get(CONFIG_ROOT)
        return bool(isinstance(section, dict) and section.get("enabled"))
    except Exception:
        return False


def _build_platform_store() -> ConnectorSecretStore:
    import sys
    if sys.platform == "win32":
        from agent.connector_secrets_dpapi import DpapiConnectorSecretStore
        return DpapiConnectorSecretStore()
    return _NoSecureBackendStore(
        "no OS secure-storage backend is implemented on this platform yet "
        "(Windows DPAPI is; macOS Keychain / Linux Secret Service come later). "
        "New connector secrets need a supported platform; existing plaintext "
        "credentials keep working.")


def connector_secret_store_status() -> Tuple[bool, str]:
    """(usable, safe reason) for UI/diagnostics. ``disabled`` while the option is off."""
    if not _connector_store_enabled():
        return False, "disabled (connector_secret_store.enabled is not set)"
    store = _build_platform_store()
    return store.is_available()


def get_connector_secret_store() -> Optional[ConnectorSecretStore]:
    """The active store for readers, or ``None`` when off / no secure backend.

    The store locates its files under the CURRENT profile home
    (``get_hermes_home()``, context-local), so under multiplexing a routed
    profile resolves its own entries, never another profile's.
    """
    if not _connector_store_enabled():
        return None
    store = _build_platform_store()
    available, _reason = store.is_available()
    return store if available else None


def get_connector_secret_store_strict() -> ConnectorSecretStore:
    """The active store for writers; raises with the exact safe reason when unusable."""
    if not _connector_store_enabled():
        raise ConnectorSecretStoreUnavailable(
            "the connector secret store is not enabled — set "
            "`connector_secret_store: {enabled: true}` in config.yaml, or keep using "
            "existing plaintext credentials (they keep working unchanged)")
    store = _build_platform_store()
    available, reason = store.is_available()
    if not available:
        raise ConnectorSecretStoreUnavailable(reason)
    return store


def resolve_connector_secret_value(server: str, field: str) -> Optional[str]:
    """Lenient resolution for config interpolation: ``None`` keeps the placeholder.

    Stores are consulted per call under the current profile home. Decrypt/store
    failures never raise past this seam — they log a value-free warning and
    return ``None`` so one broken entry cannot take down the whole MCP config
    load (other servers keep working; the affected server fails auth on its own).
    When decryption fails but a legacy plaintext copy survives (the
    connector-secrets migration backup, or the pre-migration ``.env`` value),
    that copy is returned instead — the one-line warning never contains the
    value or the fallback source path details.
    """
    store = get_connector_secret_store()
    if store is None:
        return None
    try:
        return store.resolve_secret(server, field)
    except ConnectorSecretDecryptError as exc:
        from agent.connector_secrets_migration import legacy_value_for_reference
        legacy = legacy_value_for_reference(server, field)
        if legacy is not None:
            logger.warning("MCP connector secret %s/%s could not be decrypted (%s); "
                           "using the retained legacy copy — re-save the secret to fix it",
                           server, field, _safe_error(exc))
            return legacy
        logger.warning("MCP connector secret %s/%s could not be decrypted (%s); keeping the "
                       "${connector:...} placeholder — re-save the secret to fix it",
                       server, field, _safe_error(exc))
        return None
    except ConnectorSecretError as exc:
        logger.warning("MCP connector secret %s/%s could not be read (%s); keeping the "
                       "${connector:...} placeholder", server, field, exc)
        return None


def _safe_error(exc: Exception) -> str:
    """Value-free error text for warnings (backends already sanitize; belt-and-braces
    against an exception string ever embedding decrypted material)."""
    return exc.__class__.__name__
