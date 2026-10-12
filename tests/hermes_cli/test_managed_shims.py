"""The managed ``stardust.cmd`` / ``hermes.cmd`` shims delegate to the install's venv console script.

Contract: CRLF body with the marker line second, delegating to ``<venv>\\Scripts\\stardust.exe`` with the
caller's arguments; a shim that already matches is left alone; a same-named file without the marker is
backed up first; a shim is never pointed at a script that does not exist. A launcher or shim that cannot be
moved or written is reported in the result and logged, and the start entry exits non-zero for it.
Host-independent: the Windows layout is passed explicitly and STARDUST_HOME points at a temp home.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli._install_repair import MANAGED_SHIM_MARKER, _main, refresh_managed_shims


@pytest.fixture
def install(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "hermes-agent"  # the managed clone: directly under the default root
    monkeypatch.setenv("STARDUST_HOME", str(home))

    def make_venv(venv_name: str = "venv") -> Path:
        exe = root / venv_name / "Scripts" / "stardust.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ console script")
        (exe.parent / "python.exe").write_bytes(b"MZ interpreter")
        return exe

    return home, root, make_venv


def _body(exe: Path) -> bytes:
    return b"@echo off\r\n" + MANAGED_SHIM_MARKER.encode() + b"\r\n\"" + str(exe).encode() + b"\" %*\r\n"


def test_writes_both_shims_with_marker_and_crlf_delegating_to_the_venv_script(install):
    home, root, make_venv = install
    exe = make_venv("venv")

    written = refresh_managed_shims(root, windows=True).written

    assert sorted(Path(p).name for p in written) == ["hermes.cmd", "stardust.cmd"]
    for name in ("stardust", "hermes"):
        assert (home / "bin" / f"{name}.cmd").read_bytes() == _body(exe)


def test_dotvenv_install_points_the_shims_at_the_dotvenv_script(install):
    home, root, make_venv = install
    exe = make_venv(".venv")

    refresh_managed_shims(root, windows=True)

    assert (home / "bin" / "stardust.cmd").read_bytes() == _body(exe)
    assert b"\\.venv\\Scripts\\stardust.exe" in (home / "bin" / "stardust.cmd").read_bytes() or (
        b"/.venv/Scripts/stardust.exe" in (home / "bin" / "stardust.cmd").read_bytes())


def test_current_shims_are_left_alone(install):
    home, root, make_venv = install
    make_venv("venv")
    refresh_managed_shims(root, windows=True)
    before = {p.name: p.read_bytes() for p in (home / "bin").iterdir()}

    assert refresh_managed_shims(root, windows=True).written == []

    assert {p.name: p.read_bytes() for p in (home / "bin").iterdir()} == before
    assert not list((home / "bin").glob("*.bak-*"))


def test_foreign_shim_is_backed_up_before_it_is_replaced(install):
    home, root, make_venv = install
    exe = make_venv("venv")
    (home / "bin").mkdir(parents=True)
    foreign = b"@echo off\r\n\"D:\\Stardust\\hermes-agent\\venv\\Scripts\\hermes.exe\" %*\r\n"
    (home / "bin" / "hermes.cmd").write_bytes(foreign)

    written = refresh_managed_shims(root, windows=True).written

    assert str(home / "bin" / "hermes.cmd") in written
    backups = list((home / "bin").glob("hermes.cmd.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == foreign
    assert (home / "bin" / "hermes.cmd").read_bytes() == _body(exe)


def test_our_stale_shim_is_rewritten_without_a_backup(install):
    home, root, make_venv = install
    exe = make_venv("venv")
    (home / "bin").mkdir(parents=True)
    stale = b"@echo off\r\n" + MANAGED_SHIM_MARKER.encode() + b"\r\n\"C:\\gone\\stardust.exe\" %*\r\n"
    (home / "bin" / "stardust.cmd").write_bytes(stale)

    refresh_managed_shims(root, windows=True)

    assert (home / "bin" / "stardust.cmd").read_bytes() == _body(exe)
    assert not list((home / "bin").glob("*.bak-*"))


def test_stale_managed_launchers_are_moved_aside_so_the_cmd_shims_take_effect(install):
    home, root, make_venv = install
    make_venv()
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    stale = {name: bin_dir / f"{name}.exe" for name in ("stardust", "hermes")}
    for name, path in stale.items():
        path.write_bytes(f"MZ stale {name} launcher".encode("ascii"))

    written = refresh_managed_shims(root, windows=True).written

    assert str(bin_dir / "stardust.cmd") in written
    assert (bin_dir / "stardust.cmd").read_bytes() == _body(root / "venv" / "Scripts" / "stardust.exe")
    for name, path in stale.items():
        assert not path.exists()
        backups = list(bin_dir.glob(f"{name}.exe.bak-*"))
        assert [backup.read_bytes() for backup in backups] == [f"MZ stale {name} launcher".encode("ascii")]


def test_nothing_is_written_when_the_venv_script_is_missing(install):
    home, root, _make_venv = install
    (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_bytes(b"MZ interpreter")  # no stardust.exe

    assert refresh_managed_shims(root, windows=True).written == []
    assert not (home / "bin").exists()


def test_posix_hosts_are_untouched(install):
    home, root, make_venv = install
    make_venv("venv")

    assert refresh_managed_shims(root, windows=False).written == []
    assert not (home / "bin").exists()


def test_unusable_bin_location_is_reported_not_raised(install):
    home, root, make_venv = install
    make_venv("venv")
    (home / "bin").write_text("a file, not a folder")

    result = refresh_managed_shims(root, windows=True)

    assert result.ok is False
    assert result.failed == [str(home / "bin")]
    assert result.written == []


def _make_checkout(base: Path, name: str = "hermes-agent") -> tuple[Path, Path]:
    """A checkout with a venv console script, wherever *base* is."""
    root = base / name
    exe = root / "venv" / "Scripts" / "stardust.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ console script")
    (exe.parent / "python.exe").write_bytes(b"MZ interpreter")
    return root, exe


def test_non_ascii_install_root_is_encoded_for_cmd(install, tmp_path):
    home, _root, _make_venv = install
    root, exe = _make_checkout(tmp_path / "用户")
    codec = "oem" if sys.platform == "win32" else "utf-8"

    refresh_managed_shims(root, windows=True, install_authority=True)

    shim = (home / "bin" / "stardust.cmd").read_bytes()
    assert shim.endswith(f'"{exe}" %*\r\n'.encode(codec))


def test_a_development_checkout_never_retargets_the_managed_shims(install, tmp_path):
    home, root, make_venv = install
    managed_exe = make_venv("venv")
    refresh_managed_shims(root, windows=True)
    before = {p.name: p.read_bytes() for p in (home / "bin").iterdir()}
    dev_root, _dev_exe = _make_checkout(tmp_path / "src")

    assert refresh_managed_shims(dev_root, windows=True).written == []

    assert {p.name: p.read_bytes() for p in (home / "bin").iterdir()} == before
    assert (home / "bin" / "stardust.cmd").read_bytes() == _body(managed_exe)


def test_desktop_install_authority_retargets_a_checkout_outside_the_home(install, tmp_path):
    home, _root, _make_venv = install
    dev_root, dev_exe = _make_checkout(tmp_path / "desktop-install")

    written = refresh_managed_shims(dev_root, windows=True, install_authority=True).written

    assert len(written) == 2
    assert (home / "bin" / "stardust.cmd").read_bytes() == _body(dev_exe)


def test_desktop_start_entry_exits_zero_and_rejects_bad_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    root = tmp_path / "hermes-agent"
    root.mkdir()

    ok = subprocess.run(
        [sys.executable, "-m", "hermes_cli._install_repair", "--refresh-managed-shims", str(root)],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "STARDUST_HOME": str(tmp_path / "home")},
        capture_output=True,
        timeout=120,
    )
    bad = subprocess.run(
        [sys.executable, "-m", "hermes_cli._install_repair", "--nope"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        timeout=120,
    )

    assert ok.returncode == 0, ok.stderr
    assert bad.returncode == 2


@pytest.mark.windows_only
def test_a_launcher_that_cannot_be_moved_is_logged_and_fails_the_refresh(install, caplog):
    home, root, make_venv = install
    make_venv("venv")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    locked = bin_dir / "stardust.exe"
    locked.write_bytes(b"MZ stale stardust launcher")

    # Holding the file open keeps Windows from renaming it, the same way a running launcher does.
    with locked.open("rb"), caplog.at_level(logging.WARNING, logger="hermes_cli._install_repair"):
        result = refresh_managed_shims(root, windows=True)

    assert result.ok is False
    assert result.failed == [str(locked)]
    assert locked.read_bytes() == b"MZ stale stardust launcher"
    assert any(
        record.levelno == logging.WARNING and "stardust.exe" in record.getMessage() for record in caplog.records
    )
    assert (bin_dir / "stardust.cmd").is_file()  # the shim is still written; the launcher still wins until released

    released = refresh_managed_shims(root, windows=True)

    assert released.ok is True
    assert not locked.exists()


@pytest.mark.windows_only
def test_the_desktop_start_entry_exits_nonzero_when_a_launcher_cannot_be_moved(install):
    home, root, make_venv = install
    make_venv("venv")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    locked = bin_dir / "hermes.exe"
    locked.write_bytes(b"MZ stale hermes launcher")

    with locked.open("rb"):
        assert _main(["--refresh-managed-shims", str(root)]) == 1
