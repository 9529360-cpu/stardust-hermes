"""Native regression coverage for the Windows Desktop hand-off's venv layout (`venv` or `.venv`).

The hand-off drives the interpreter it finds through `Resolve-VenvDir`, and `Ensure-VenvAlias` exposes a
`.venv`-only install as a `venv` junction before the prerequisite check. These tests run the real script
through its `-SelfTestVenvLayout` switch, the same pattern as the cwd suite, so the PowerShell functions run
exactly as the hand-off runs them.

Junctions are removed with `os.rmdir` before the test ends: that drops the link, never the `.venv` it points
at. Python 3.11's `shutil.rmtree` follows a directory junction on Windows, so pytest's own cleanup must never
meet a live one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.windows_only

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WINDOWS_UPDATE_PS1 = REPO_ROOT / "scripts" / "desktop-update" / "windows.ps1"
RESULT = re.compile(r"VENV-LAYOUT SELF-TEST: PASS alias=(?P<alias>\S+) venv=(?P<venv>.+)")


def _run_venv_layout_self_test(install_root: Path, temp_dir: Path) -> tuple[str, Path]:
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows updater tests require Windows PowerShell."
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_UPDATE_PS1),
            "-InstallRoot",
            str(install_root),
            "-SelfTestVenvLayout",
            "-NoUi",
        ],
        cwd=install_root.parent,
        env=env,
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    match = RESULT.search(result.stdout)
    assert match, result.stdout
    return match.group("alias"), Path(match.group("venv").strip())


def _interpreter(venv_dir: Path) -> Path:
    python = venv_dir / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"interpreter fixture")
    return python


def _remove_junction(alias: Path) -> None:
    """Drop a junction (or an empty folder) without following it into .venv."""
    if os.path.lexists(alias):
        os.rmdir(alias)


def test_dotvenv_only_install_gets_a_working_venv_junction(tmp_path: Path) -> None:
    root = tmp_path / "install"
    python = _interpreter(root / ".venv")
    alias = root / "venv"
    try:
        state, venv = _run_venv_layout_self_test(root, tmp_path / "temp")

        assert state == "created"
        assert venv == alias
        assert (alias / "Scripts" / "python.exe").read_bytes() == python.read_bytes()
        assert python.read_bytes() == b"interpreter fixture"  # .venv itself is untouched

        second_state, second_venv = _run_venv_layout_self_test(root, tmp_path / "temp")
        assert second_state == "present"
        assert second_venv == alias
    finally:
        _remove_junction(alias)


def test_venv_wins_when_both_folders_hold_an_interpreter(tmp_path: Path) -> None:
    root = tmp_path / "install"
    _interpreter(root / "venv")
    _interpreter(root / ".venv")

    state, venv = _run_venv_layout_self_test(root, tmp_path / "temp")

    assert state == "present"
    assert venv == root / "venv"


def test_fresh_install_without_any_interpreter_defaults_to_venv(tmp_path: Path) -> None:
    root = tmp_path / "install"
    root.mkdir()

    state, venv = _run_venv_layout_self_test(root, tmp_path / "temp")

    assert state == "no-dotvenv"
    assert venv == root / "venv"


def test_half_repaired_venv_folder_does_not_hide_a_working_dotvenv(tmp_path: Path) -> None:
    root = tmp_path / "install"
    (root / "venv" / "Lib").mkdir(parents=True)  # a folder with no interpreter
    _interpreter(root / ".venv")

    state, venv = _run_venv_layout_self_test(root, tmp_path / "temp")

    assert state == "present"
    assert venv == root / ".venv"
    assert (root / "venv" / "Lib").is_dir()  # the half-repaired folder is left alone


def test_dangling_venv_junction_is_reported_present_not_replaced(tmp_path: Path) -> None:
    root = tmp_path / "install"
    dotvenv = root / ".venv"
    _interpreter(dotvenv)
    alias = root / "venv"
    try:
        assert _run_venv_layout_self_test(root, tmp_path / "temp")[0] == "created"
        # Remove the target out from under the junction: the alias now dangles.
        (dotvenv / "Scripts" / "python.exe").unlink()
        (dotvenv / "Scripts").rmdir()
        dotvenv.rmdir()

        state, _venv = _run_venv_layout_self_test(root, tmp_path / "temp")

        assert state == "present"
        assert os.path.lexists(alias)
    finally:
        _remove_junction(alias)
