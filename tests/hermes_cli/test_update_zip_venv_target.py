from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_cli.update_cmd_zip as update_cmd_zip
from hermes_cli import update_cmd


def _make_dual_venv_layout(root: Path) -> tuple[Path, Path]:
    paths = []
    for name in (".venv", "venv"):
        env_root = root / name
        python = env_root / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fixture")
        paths.append(python)
    return paths[0], paths[1]


def test_zip_swap_preserves_both_venv_layouts(tmp_path, monkeypatch):
    project_root = tmp_path / "checkout"
    project_root.mkdir()
    for name in (".venv", "venv"):
        env_root = project_root / name
        env_root.mkdir()
        (env_root / "user-state.txt").write_text(f"keep-{name}", encoding="utf-8")

    def write_archive(_url: str, destination: str) -> None:
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("hermes-agent-main/.venv/replacement.txt", "new-dot")
            archive.writestr("hermes-agent-main/venv/replacement.txt", "new-flat")
            archive.writestr("hermes-agent-main/update-marker.txt", "updated")

    fake_main = SimpleNamespace(
        PROJECT_ROOT=project_root,
        _zip_overlay_block_reason=lambda *_args, **_kwargs: None,
        sys=sys,
    )
    monkeypatch.setattr(update_cmd, "_m", lambda: fake_main)
    monkeypatch.setattr("urllib.request.urlretrieve", write_archive)

    update_cmd_zip._download_and_swap_zip("main", "https://example.invalid/archive.zip")

    assert (project_root / ".venv" / "user-state.txt").read_text(encoding="utf-8") == "keep-.venv"
    assert (project_root / "venv" / "user-state.txt").read_text(encoding="utf-8") == "keep-venv"
    assert not (project_root / ".venv" / "replacement.txt").exists()
    assert not (project_root / "venv" / "replacement.txt").exists()
    assert (project_root / "update-marker.txt").read_text(encoding="utf-8") == "updated"


def test_zip_dependency_reinstall_targets_active_dot_venv(tmp_path, monkeypatch):
    dot_python, _formal_python = _make_dual_venv_layout(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "outer-python.exe"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    calls = []
    fake_main = SimpleNamespace(
        PROJECT_ROOT=tmp_path,
        sys=sys,
        _is_windows=lambda: True,
        _is_termux_env=lambda _env: False,
        _install_python_dependencies_with_optional_fallback=lambda prefix, *, env: calls.append((prefix, env)),
        _restore_active_tool_dependencies=lambda *_args, **_kwargs: None,
        _refresh_active_memory_provider_dependencies=lambda: None,
    )
    monkeypatch.setattr(update_cmd, "_m", lambda: fake_main)
    monkeypatch.setattr("hermes_cli.managed_uv.ensure_uv", lambda: "uv")
    monkeypatch.setattr("hermes_cli.managed_uv.update_managed_uv", lambda: None)

    update_cmd_zip._reinstall_python_deps_after_zip([])

    assert calls[0][0] == ["uv", "pip"]
    assert calls[0][1]["VIRTUAL_ENV"] == str(tmp_path / ".venv")
def test_zip_dependency_failure_leaves_recovery_marker_armed(tmp_path, monkeypatch):
    marker = {"armed": False}
    fake_main = SimpleNamespace(
        PROJECT_ROOT=tmp_path,
        sys=sys,
        _capture_active_tool_dependencies=lambda: [],
        _resolve_update_branch=lambda _args: "main",
        _abort_dependency_sync_if_self_locked=lambda *_args, **_kwargs: None,
        _write_update_incomplete_marker=lambda: marker.__setitem__("armed", True),
        _clear_update_incomplete_marker=lambda: marker.__setitem__("armed", False),
    )
    monkeypatch.setattr(update_cmd, "_m", lambda: fake_main)
    monkeypatch.setattr(update_cmd, "_read_project_version", lambda: None)
    monkeypatch.setattr(update_cmd, "_sweep_bytecode_after_update", lambda *_args: None)
    monkeypatch.setattr(update_cmd_zip, "_abort_zip_update_if_dirty_tree", lambda: None)
    monkeypatch.setattr(update_cmd_zip, "_download_and_swap_zip", lambda *_args: None)

    def fail_dependencies(_active_tool_dependencies):
        assert marker["armed"]
        raise RuntimeError("simulated package install failure")

    monkeypatch.setattr(update_cmd_zip, "_reinstall_python_deps_after_zip", fail_dependencies)

    with pytest.raises(RuntimeError, match="simulated package install failure"):
        update_cmd_zip._update_via_zip(SimpleNamespace(branch="main"))

    assert marker["armed"]
