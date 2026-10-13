"""Windows updater uses one explicitly selected virtual environment."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WINDOWS_PS1 = REPO_ROOT / "scripts" / "desktop-update" / "windows.ps1"
MAIN_TS = REPO_ROOT / "apps" / "desktop" / "electron" / "main.ts"


def test_windows_handoff_accepts_explicit_environment_root_and_python() -> None:
    source = WINDOWS_PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
    parameters = source.split("param(", 1)[1].split(")", 1)[0]

    assert "[string]$VenvRoot" in parameters
    assert "[string]$PythonPath" in parameters


def test_preflight_shim_lock_and_cli_execution_share_the_selected_environment() -> None:
    source = WINDOWS_PS1.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert 'Join-Path $InstallRoot "venv\\Scripts\\python.exe"' not in source
    assert 'Join-Path $InstallRoot "venv\\Scripts\\stardust.exe"' not in source
    assert 'Join-Path $VenvRoot "Scripts\\python.exe"' in source
    assert 'Join-Path $VenvRoot "Scripts\\stardust.exe"' in source
    assert "Invoke-HermesStep $pythonExe $updateArgs" in source
    assert '& $pythonExe -m hermes_cli.main update --help' in source


def test_electron_handoff_passes_resolver_selected_paths() -> None:
    source = MAIN_TS.read_text(encoding="utf-8")
    wrapped = source.split("const wrapped = wrapHandoffForDetachedConsole(scriptHandoff, [", 1)[1].split(
        "])\n\n      child", 1
    )[0]

    assert "'-VenvRoot'" in wrapped
    assert "updateVenvRoot" in wrapped
    assert "'-PythonPath'" in wrapped
    assert "updatePythonPath" in wrapped


def _run_venv_self_test(install_root: Path, venv_root: Path, python_path: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is required for the native path self-test")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_PS1),
            "-InstallRoot",
            str(install_root),
            "-VenvRoot",
            str(venv_root),
            "-PythonPath",
            str(python_path),
            "-SelfTestVenvPaths",
            "-NoUi",
            "-NoMarkerCleanup",
        ],
        cwd=REPO_ROOT,
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )


def test_powershell_self_test_uses_dot_venv_python_and_shim(tmp_path: Path) -> None:
    install_root = tmp_path / "checkout"
    venv_root = install_root / ".venv"
    python_path = venv_root / "Scripts" / "python.exe"

    install_root.mkdir()
    result = _run_venv_self_test(install_root, venv_root, python_path)

    assert result.returncode == 0, result.stdout
    assert "VENV TARGET SELF-TEST: PASS" in result.stdout
    assert str(python_path) in result.stdout
    assert str(venv_root / "Scripts" / "stardust.exe") in result.stdout


def test_powershell_rejects_python_from_a_different_environment(tmp_path: Path) -> None:
    install_root = tmp_path / "checkout"
    venv_root = install_root / ".venv"
    python_path = install_root / "venv" / "Scripts" / "python.exe"

    install_root.mkdir()
    result = _run_venv_self_test(install_root, venv_root, python_path)

    assert result.returncode == 3, result.stdout
    assert "does not belong to the selected environment" in result.stdout
