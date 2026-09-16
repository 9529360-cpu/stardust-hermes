"""Stable opaque identity shared by every profile in one Hermes install."""

from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
import re
import threading
import time
from typing import Optional
import uuid

from hermes_constants import get_default_hermes_root
from utils import atomic_write_text

_INSTALL_ID_FILENAME = "install_id"
_INSTALL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_INSTALL_ID_CACHE: dict[str, Optional[str]] = {"root": None, "value": None}
_INSTALL_ID_LOCK = threading.Lock()
_INSTALL_ID_FILE_LOCK_TIMEOUT_S = 5.0
_LOCK_CONTENTION_ERRNOS = frozenset({
    errno.EWOULDBLOCK,
    errno.EAGAIN,
    errno.EACCES,
    errno.EDEADLK,
})


def _is_lock_contention_errno(exc: OSError) -> bool:
    """True only for a lock already owned by another thread/process."""
    return exc.errno in _LOCK_CONTENTION_ERRNOS


def _lock_retry_sleep(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    time.sleep(min(0.05, remaining))


@contextlib.contextmanager
def _install_id_file_lock(root: Path, *, timeout: float | None = None):
    """Serialize identity publication across threads/processes on POSIX and Windows.

    The install id is an authority value, so contention must never cause a second identity to be
    minted.  At the same time, a wedged publisher must not block every caller forever.  Use a
    single bounded non-blocking file-lock loop for both thread and process contention; timeout
    surfaces as ``TimeoutError`` and the public creation path returns ``None`` so a later call can
    retry safely.
    """
    wait = _INSTALL_ID_FILE_LOCK_TIMEOUT_S if timeout is None else max(0.0, float(timeout))
    deadline = time.monotonic() + wait
    fd = os.open(root / ".install_id.lock", os.O_RDWR | os.O_CREAT, 0o600)
    windows = os.name == "nt"
    acquired = False
    try:
        if windows:
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if not _is_lock_contention_errno(exc):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for install identity publication lock")
                    _lock_retry_sleep(deadline)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if not _is_lock_contention_errno(exc):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for install identity publication lock")
                    _lock_retry_sleep(deadline)
        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                if windows:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_existing(path: Path) -> tuple[Optional[str], bool]:
    """``(valid id or None, mint?)`` — mint on a missing or malformed file, never on a read failure."""
    try:
        existing = path.read_text(encoding="utf-8").strip().lower()
    except FileNotFoundError:
        return None, True
    except (OSError, UnicodeDecodeError):
        return None, False
    return (existing, False) if _INSTALL_ID_RE.fullmatch(existing) else (None, True)


def read_or_create_install_id(root: Path | None = None) -> Optional[str]:
    """Read or atomically mint the opaque id for the physical install.

    ``None`` = neither readable nor safely persistable right now; an ephemeral or competing id
    would violate the authority/registry contract.  Lock timeout therefore fails closed to None.
    """
    root = get_default_hermes_root() if root is None else root
    path = root / _INSTALL_ID_FILENAME
    existing, mint = _read_existing(path)
    if not mint:
        return existing
    try:
        root.mkdir(parents=True, exist_ok=True)
        with _install_id_file_lock(root):
            existing, mint = _read_existing(path)
            if not mint:
                return existing
            atomic_write_text(path, uuid.uuid4().hex + "\n", tmp_prefix=".install_id-", fsync_dir=True, mode=0o600)
            committed = path.read_text(encoding="utf-8").strip().lower()
            return committed if _INSTALL_ID_RE.fullmatch(committed) else None
    except OSError:
        return None


def get_install_id(*, cache: dict[str, Optional[str]] | None = None) -> Optional[str]:
    """Return the process-cached stable id for the active Hermes root."""
    root = get_default_hermes_root()
    root_key = str(root)
    target_cache = _INSTALL_ID_CACHE if cache is None else cache

    def _cached() -> Optional[str]:
        cached = target_cache.get("value")
        return cached if cached and target_cache.get("root") in (None, root_key) else None

    if value := _cached():
        return value
    with _INSTALL_ID_LOCK:
        if value := _cached():
            return value
        value = read_or_create_install_id(root)
        if value:
            target_cache["root"] = root_key
            target_cache["value"] = value
        return value


__all__ = ["get_install_id", "read_or_create_install_id"]
