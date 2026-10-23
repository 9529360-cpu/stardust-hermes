"""Windows DPAPI (CurrentUser) backend for the connector secret store.

Ciphertext binds to the Windows user that called ``CryptProtectData`` plus
optional entropy; Stardust uses no entropy so the blob is decryptable by the
same user across Stardust processes (CLI, gateway, desktop backend) — and by
nobody else, including a copied profile directory on another machine or user
account. That is the encryption guarantee the plaintext ``.env`` /
``mcp-tokens/*.json`` files never had; file permissions (0600/0700) are applied
as well but are a separate, weaker guarantee.

On-disk layout (profile-scoped by construction — the base dir lives under the
profile home)::

    <home>/connector-secrets/srv-<slug(server)>/f-<slug(field)>.json

Each entry is a JSON envelope: non-sensitive metadata (version, backend,
server/field names, timestamps) plus ``data`` — the base64 DPAPI blob. Server
and field names are slugified with percent-escapes (``%04X`` per non-safe
char), so arbitrary MCP server names cannot traverse out of the base dir or
collide with Windows reserved device names. Envelope writes are atomic
(temp file + fsync + rename, mode 0600) under a cross-process lock file, so a
desktop gateway and a CLI writer cannot corrupt each other's entry.
"""
from __future__ import annotations

import base64
import contextlib
import ctypes
import json
import logging
import os
import shutil
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from hermes_constants import get_hermes_home
from utils import atomic_write_bytes

from agent.connector_secrets import (
    ConnectorSecretDecryptError,
    ConnectorSecretEntry,
    ConnectorSecretError,
    ConnectorSecretStore,
    ConnectorSecretStoreUnavailable,
)

logger = logging.getLogger("agent.connector_secrets")

_ENVELOPE_VERSION = 1
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

# Concurrency: one store dir per profile home, but the module also serves a
# multiplexed gateway (many profile homes in one process) and CLI/desktop
# writers in separate processes. Threads serialize on _LOCK; processes on the
# lock file inside the store dir (fcntl where available, msvcrt on Windows).
_LOCK = threading.Lock()
msvcrt = None
fcntl = None
try:
    import fcntl  # noqa: F401  (POSIX; also lets tests import this module there)
except ImportError:  # pragma: no cover — platform-specific
    with contextlib.suppress(ImportError):
        import msvcrt

# Bound (CryptProtectData, CryptUnprotectData, DATA_BLOB class) or None on a
# non-Windows host. Lazy so importing this module is safe everywhere and tests
# can monkeypatch _dpapi_protect/_dpapi_unprotect before the first real call.
_crypt32: Optional[tuple] = None
_crypt32_bound = False


def _bind_crypt32() -> Optional[tuple]:
    """Bind crypt32's Crypt{Protect,Unprotect}Data once; None off Windows."""
    global _crypt32, _crypt32_bound
    if not _crypt32_bound:
        _crypt32_bound = True
        if sys.platform == "win32":
            import ctypes.wintypes

            class _DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", ctypes.wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.wintypes.BYTE))]

            try:
                crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            except OSError as exc:
                raise ConnectorSecretStoreUnavailable(
                    f"Windows crypt32.dll could not be loaded: {exc}") from exc
            protect = crypt32.CryptProtectData
            protect.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD,
                                ctypes.POINTER(_DATA_BLOB)]
            protect.restype = ctypes.wintypes.BOOL
            unprotect = crypt32.CryptUnprotectData
            unprotect.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p),
                                  ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
            unprotect.restype = ctypes.wintypes.BOOL
            _crypt32 = (protect, unprotect, _DATA_BLOB)
    return _crypt32


def _dpapi_call(func, data: bytes) -> bytes:
    api = _bind_crypt32()
    if api is None:
        raise ConnectorSecretStoreUnavailable(
            "Windows DPAPI is unavailable: this build only provides the connector secret "
            "store backend on Windows")
    import ctypes
    import ctypes.wintypes
    _protect, _unprotect, blob_type = api
    in_buf = ctypes.create_string_buffer(data, len(data))
    in_blob = blob_type(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.wintypes.BYTE)))
    out_blob = blob_type()
    if not func(ctypes.byref(in_blob), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)):
        raise ConnectorSecretError(
            f"Windows DPAPI call failed (WinError {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_protect(plaintext: bytes) -> bytes:
    """CryptProtectData (CurrentUser). Raises ConnectorSecretError — never falls back."""
    api = _bind_crypt32()
    if api is None:
        raise ConnectorSecretStoreUnavailable(
            "Windows DPAPI is unavailable: this build only provides the connector secret "
            "store backend on Windows")
    try:
        return _dpapi_call(api[0], plaintext)
    except ConnectorSecretStoreUnavailable:
        raise
    except ConnectorSecretError:
        raise ConnectorSecretStoreUnavailable(
            "Windows DPAPI could not encrypt (CryptProtectData failed); refusing to store "
            "the secret in any weaker form")


def _dpapi_unprotect(blob: bytes) -> bytes:
    """CryptUnprotectData. Raises ConnectorSecretDecryptError on any failure."""
    api = _bind_crypt32()
    if api is None:
        raise ConnectorSecretStoreUnavailable(
            "Windows DPAPI is unavailable: this build only provides the connector secret "
            "store backend on Windows")
    try:
        return _dpapi_call(api[1], blob)
    except ConnectorSecretStoreUnavailable:
        raise
    except ConnectorSecretError:
        raise ConnectorSecretDecryptError(
            "Windows DPAPI could not decrypt this entry (wrong Windows user, corrupted "
            "store, or a profile copied from another machine)")


def _slug(name: str) -> str:
    """Filesystem-safe stable encoding of a server/field name.

    Safe ASCII stays verbatim; every other char becomes ``%04X``. ``srv-`` /
    ``f-`` prefixes (added by the callers) keep the result from ever being
    ``.``/``..`` or a Windows reserved device name (CON, COM1, ...).
    """
    out: List[str] = []
    for ch in name:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in "._-":
            out.append(ch)
        else:
            out.append("%%%04X" % ord(ch))
    slug = "".join(out)
    if not slug or slug.strip(".") == "":
        raise ConnectorSecretError("connector secret names must contain more than dots")
    return slug


class DpapiConnectorSecretStore(ConnectorSecretStore):
    """DPAPI-encrypted, profile-scoped connector secret store (see module docstring)."""

    backend_name = "windows-dpapi-currentuser"

    def __init__(self, base_dir: Optional[Path] = None):
        # Resolved per instance (never cached at module level): each resolution
        # follows the current profile home, so a multiplexed gateway serving
        # profile B never reads profile A's entries.
        self._base = Path(base_dir) if base_dir is not None else get_hermes_home() / "connector-secrets"

    # -- availability ------------------------------------------------------

    def is_available(self) -> Tuple[bool, str]:
        """Live protect/unprotect round-trip (nothing persisted)."""
        probe = b"stardust-connector-secret-store-probe"
        try:
            if _dpapi_unprotect(_dpapi_protect(probe)) == probe:
                return True, self.backend_name
            return False, "Windows DPAPI round-trip returned mismatched data"
        except ConnectorSecretError as exc:
            return False, str(exc)

    # -- layout / locking ----------------------------------------------------

    def _entry_path(self, server: str, field: str) -> Path:
        return self._base / ("srv-" + _slug(server)) / ("f-" + _slug(field) + ".json")

    def _ensure_dir(self, path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Route through the canonical securer (managed/NixOS group-share mode),
        # same as agent/vault_store.py; plain chmod as the fallback.
        try:
            from hermes_cli.config import _secure_dir
            _secure_dir(self._base)
        except Exception:
            with contextlib.suppress(OSError):
                os.chmod(self._base, 0o700)

    @contextmanager
    def _locked(self):
        """Serialize entry reads/writes across threads and processes (see _LOCK)."""
        with _LOCK:
            self._base.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = self._base / ".lock"
            if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
                lock_path.write_text(" ", encoding="utf-8")  # msvcrt needs a non-empty byte range
            with open(lock_path, "r+" if msvcrt else "a+", encoding="utf-8") as fd:
                if fcntl:  # pragma: no cover — POSIX backend host
                    fcntl.flock(fd, fcntl.LOCK_EX)
                elif msvcrt:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    with contextlib.suppress(OSError):
                        if fcntl:  # pragma: no cover — POSIX backend host
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        elif msvcrt:
                            fd.seek(0)
                            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _read_envelope(path: Path) -> Optional[dict]:
        """Parsed envelope, or None when absent/unreadable/not valid JSON."""
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return envelope if isinstance(envelope, dict) else None

    # -- management contract -------------------------------------------------

    def store_secret(self, server: str, field: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ConnectorSecretError("secret value must be a non-empty string")
        # Encrypt FIRST: with no usable backend nothing is created on disk —
        # the fail-closed guarantee is that no file (plaintext or otherwise)
        # appears from a refused save.
        cipher = _dpapi_protect(value.encode("utf-8"))
        entry_path = self._entry_path(server, field)
        now = datetime.now(timezone.utc).isoformat()
        with self._locked():
            existing = self._read_envelope(entry_path)
            # A corrupt/unreadable envelope keeps only its place on disk; the
            # rewrite below replaces it wholesale (created_at restarts).
            created_at = existing.get("created_at") if existing else None
            envelope = {
                "version": _ENVELOPE_VERSION,
                "backend": self.backend_name,
                "server": server,
                "field": field,
                "created_at": created_at or now,
                "updated_at": now,
                "data": base64.b64encode(cipher).decode("ascii"),
            }
            self._ensure_dir(entry_path.parent)
            atomic_write_bytes(entry_path, json.dumps(envelope, indent=2).encode("utf-8"),
                               mode=0o600, fsync_dir=True)

    def resolve_secret(self, server: str, field: str) -> Optional[str]:
        entry_path = self._entry_path(server, field)
        with self._locked():
            envelope = self._read_envelope(entry_path)
        if envelope is None:
            return None
        if envelope.get("version") != _ENVELOPE_VERSION:
            raise ConnectorSecretDecryptError(
                f"unsupported connector secret envelope version {envelope.get('version')!r}")
        if envelope.get("backend") != self.backend_name:
            raise ConnectorSecretDecryptError(
                f"entry was written by backend {envelope.get('backend')!r}, not "
                f"{self.backend_name!r}")
        try:
            cipher = base64.b64decode(envelope["data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorSecretDecryptError(
                "entry envelope is damaged (missing/invalid ciphertext)") from exc
        plaintext = _dpapi_unprotect(cipher)
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorSecretDecryptError("decrypted value is not valid UTF-8") from exc

    def delete_secret(self, server: str, field: str) -> bool:
        entry_path = self._entry_path(server, field)
        with self._locked():
            existed = entry_path.exists()
            with contextlib.suppress(OSError):
                entry_path.unlink()
            # Drop the server dir too when its last entry is gone, so profiles
            # don't accumulate empty connector directories after disconnects.
            with contextlib.suppress(OSError):
                entry_path.parent.rmdir()
            return existed

    def delete_server(self, server: str) -> int:
        server_dir = self._base / ("srv-" + _slug(server))
        with self._locked():
            removed = 0
            if server_dir.is_dir():
                removed = sum(1 for p in server_dir.glob("f-*.json") if p.is_file())
                shutil.rmtree(server_dir, ignore_errors=True)
            return removed

    def list_entries(self) -> List[ConnectorSecretEntry]:
        entries: List[ConnectorSecretEntry] = []
        with self._locked():
            if not self._base.is_dir():
                return entries
            for server_dir in sorted(self._base.iterdir()):
                if not server_dir.is_dir() or not server_dir.name.startswith("srv-"):
                    continue
                for entry_path in sorted(server_dir.glob("f-*.json")):
                    envelope = self._read_envelope(entry_path)
                    if envelope is None:
                        logger.warning("connector secret entry file %s is unreadable; skipping",
                                       entry_path.name)
                        continue
                    entries.append(ConnectorSecretEntry(
                        server=str(envelope.get("server", server_dir.name)),
                        field=str(envelope.get("field", entry_path.stem[2:])),
                        created_at=str(envelope.get("created_at", "")),
                        updated_at=str(envelope.get("updated_at", "")),
                    ))
        return entries
