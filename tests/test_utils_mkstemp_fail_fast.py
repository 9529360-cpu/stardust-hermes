"""``utils.mkstemp_fail_fast`` — temp files that fail fast instead of spinning on Windows.

CPython's ``tempfile.mkstemp`` (and ``mkdtemp``) treat EVERY ``PermissionError`` on Windows as "a
directory with this name already exists" whenever ``os.access(dir, os.W_OK)`` is true — and on
Windows ``os.access`` only looks at the read-only attribute, never the ACL. A directory whose ACL
denies file creation therefore retries ``TMP_MAX`` (2**31 - 1 on Windows) names: a CPU-bound loop
that never returns.

Field failure (2026-10-01): ``logs/process-results`` had been created ``mode=0o700`` by an elevated
process, so Python gave it an owner-only DACL the normal (non-elevated) user is not in. Three
finished kanban workers then spun for hours inside ``save_completed_result`` — while holding the
process-registry lock — so they never exited, burned ~2 CPU cores, and kept the venv locked, which
aborted every Desktop update.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import utils
from utils import atomic_json_write, mkstemp_fail_fast

REPO_ROOT = Path(__file__).resolve().parent.parent


def _current_user_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, check=True).stdout
    match = re.search(rb"S-1-[0-9-]+", out)
    assert match, out
    return match.group().decode("ascii")


@contextmanager
def deny_file_creation(directory: Path):
    """Deny the current user "add file" / "add subdirectory" on *directory* (Windows ACL).

    Reading and listing stay allowed, so ``os.path.isdir`` and ``os.access(W_OK)`` both still say the
    directory is usable — exactly the state that sends CPython's ``mkstemp`` into its retry loop.
    """
    sid = _current_user_sid()
    subprocess.run(["icacls", str(directory), "/deny", f"*{sid}:(WD,AD)"], capture_output=True, check=True)
    try:
        yield
    finally:
        subprocess.run(["icacls", str(directory), "/remove:d", f"*{sid}"], capture_output=True, check=True)


def test_creates_an_exclusive_private_file_like_mkstemp(tmp_path):
    fd, path = mkstemp_fail_fast(dir=tmp_path, prefix=".probe_", suffix=".tmp")
    try:
        os.write(fd, b"payload")
    finally:
        os.close(fd)
    created = Path(path)
    assert created.is_absolute() and created.parent == tmp_path
    assert created.name.startswith(".probe_") and created.name.endswith(".tmp")
    assert created.read_bytes() == b"payload"
    if os.name != "nt":
        assert stat.S_IMODE(created.stat().st_mode) == 0o600


def test_access_denied_raises_on_the_first_attempt(tmp_path, monkeypatch):
    attempts: list[str] = []

    def denied(path, flags, mode=0o777):
        attempts.append(os.fspath(path))
        raise PermissionError(13, "Access is denied", os.fspath(path))

    monkeypatch.setattr(utils.os, "open", denied)
    with pytest.raises(PermissionError):
        mkstemp_fail_fast(dir=tmp_path, prefix=".probe_")
    assert len(attempts) == 1


def test_a_directory_with_the_chosen_name_is_skipped_not_fatal(tmp_path, monkeypatch):
    """Windows reports CreateFile on an existing directory as access-denied: that one is a collision."""
    names = iter(["taken", "free"])
    monkeypatch.setattr(utils.secrets, "token_hex", lambda nbytes: next(names))
    (tmp_path / ".probe_taken").mkdir()
    real_open = os.open

    def windows_like_open(path, flags, mode=0o777):
        if os.path.isdir(path):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real_open(path, flags, mode)

    monkeypatch.setattr(utils.os, "open", windows_like_open)
    fd, path = mkstemp_fail_fast(dir=tmp_path, prefix=".probe_")
    os.close(fd)
    assert Path(path).name == ".probe_free"


def test_atomic_json_write_surfaces_access_denied_and_leaves_no_temp_file(tmp_path, monkeypatch):
    def denied(path, flags, mode=0o777):
        raise PermissionError(13, "Access is denied", os.fspath(path))

    target_dir = tmp_path / "state"
    target_dir.mkdir()
    monkeypatch.setattr(utils.os, "open", denied)
    with pytest.raises(PermissionError):
        atomic_json_write(target_dir / "state.json", {"ok": True})
    assert list(target_dir.iterdir()) == []


@pytest.mark.windows_only
def test_atomic_json_write_into_an_acl_denied_directory_fails_fast(tmp_path):
    """Real NTFS ACL, real interpreter: before the fix this spun until the timeout killed it."""
    locked = tmp_path / "process-results"
    locked.mkdir()
    probe = (
        "import sys, utils\n"
        "try:\n"
        "    utils.atomic_json_write(sys.argv[1], {'ok': True})\n"
        "except PermissionError:\n"
        "    sys.exit(13)\n"
    )
    with deny_file_creation(locked):
        result = subprocess.run(
            [sys.executable, "-c", probe, str(locked / "proc_0123456789ab.json")],
            cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            capture_output=True, text=True, timeout=60,
        )
    assert result.returncode == 13, result.stdout + result.stderr
    assert list(locked.iterdir()) == []
