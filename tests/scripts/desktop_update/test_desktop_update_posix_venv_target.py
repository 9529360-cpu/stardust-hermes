from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
POSIX_SH = REPO_ROOT / "scripts" / "desktop-update" / "posix.sh"


def _layout(root: Path) -> dict[str, tuple[Path, Path]]:
    layouts = {}
    for name in (".venv", "venv"):
        bin_dir = root / name / "bin"
        bin_dir.mkdir(parents=True)
        python = bin_dir / "python"
        shim = bin_dir / "stardust"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        shim.chmod(0o755)
        layouts[name] = (python, shim)
    return layouts


def _shell_path(path: Path) -> str:
    raw = str(path)
    if len(raw) >= 3 and raw[1] == ":":
        return "/" + raw[0].lower() + raw[2:].replace("\\\\", "/").replace("\\", "/")
    return raw


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for the POSIX updater test")
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    shell_args = [_shell_path(root)]
    shell_args.extend(_shell_path(Path(arg)) if len(arg) >= 3 and arg[1] == ":" else arg for arg in args)
    return subprocess.run(
        [bash, str(POSIX_SH), "--install-root", *shell_args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )


@pytest.mark.parametrize("layout_name", [".venv", "venv"])
def test_posix_self_test_uses_explicit_venv_target(tmp_path, layout_name):
    layouts = _layout(tmp_path)
    python, shim = layouts[layout_name]

    result = _run(
        tmp_path,
        "--venv-root",
        str(tmp_path / layout_name),
        "--python-path",
        str(python),
        "--self-test-venv-paths",
    )

    assert result.returncode == 0, result.stderr
    assert "VENV TARGET SELF-TEST: PASS" in result.stdout
    assert _shell_path(tmp_path / layout_name) in result.stdout
    assert _shell_path(python) in result.stdout
    assert _shell_path(shim) in result.stdout


def test_posix_script_refuses_ambiguous_dual_layout_without_identity(tmp_path):
    _layout(tmp_path)

    result = _run(tmp_path, "--self-test-venv-paths")

    assert result.returncode != 0
    assert "ambiguous" in (result.stdout + result.stderr).lower()


def test_posix_script_rejects_python_from_a_different_environment(tmp_path):
    layouts = _layout(tmp_path)

    result = _run(
        tmp_path,
        "--venv-root",
        str(tmp_path / ".venv"),
        "--python-path",
        str(layouts["venv"][0]),
        "--self-test-venv-paths",
    )

    assert result.returncode != 0
    assert "does not belong" in (result.stdout + result.stderr).lower()
