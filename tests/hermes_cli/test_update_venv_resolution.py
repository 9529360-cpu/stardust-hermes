from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import hermes_cli._early_recovery as early_recovery
import hermes_cli._install_repair as install_repair
import hermes_cli.main as cli_main
from hermes_cli import main_install_repair, update_cmd, update_cmd_deps, update_cmd_maint


def _make_dual_layout(root: Path) -> tuple[Path, Path]:
    paths = []
    for name in (".venv", "venv"):
        scripts = root / name / "Scripts"
        scripts.mkdir(parents=True)
        python = scripts / "python.exe"
        python.write_bytes(b"fixture")
        paths.append(python)
    return paths[0], paths[1]


def _patch_active_dot_venv(monkeypatch, root: Path, python: Path) -> None:
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", root)
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setenv("VIRTUAL_ENV", str(python.parent.parent))


def test_update_pip_prefix_targets_the_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    monkeypatch.setattr("hermes_cli.managed_uv.managed_python_env", lambda: {"PATH": "fixture"})

    prefix, env = update_cmd._pip_install_prefix("uv")

    assert prefix == ["uv", "pip"]
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_main_repair_install_target_uses_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    monkeypatch.setattr("hermes_cli.managed_uv.ensure_uv", lambda: "uv")

    prefix, env = main_install_repair._default_venv_install_target()

    assert prefix == ["uv", "pip"]
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_early_repair_install_target_uses_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    monkeypatch.setattr(install_repair._er, "_find_uv_binary", lambda: "uv")

    prefix, env = install_repair._resolve_install_target(tmp_path)

    assert prefix == ["uv", "pip"]
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_main_install_repair_quarantines_shims_in_the_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)

    assert main_install_repair._venv_scripts_dir() == tmp_path / ".venv" / "Scripts"


def test_early_repair_install_target_uses_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    monkeypatch.setattr(install_repair, "_is_windows", lambda: True)

    assert install_repair._venv_scripts_dir(tmp_path) == tmp_path / ".venv" / "Scripts"


def test_early_startup_recovery_installs_into_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    monkeypatch.setattr(early_recovery, "_base_interpreter_is_externally_managed", lambda: True)
    monkeypatch.setattr(early_recovery, "_find_uv_binary", lambda: "uv")
    calls = []
    monkeypatch.setattr(
        early_recovery,
        "_run_installer",
        lambda tool, command, root, env=None: (calls.append((tool, command, root, env)), True)[1],
    )

    assert early_recovery._run_repair_install(["broken-package"], tmp_path)

    assert calls[0][3]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_desktop_rebuild_runs_under_the_selected_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "outer-python" / "python.exe"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    desktop_dir = tmp_path / "apps" / "desktop"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "package.json").write_text("{}", encoding="utf-8")
    commands = []
    monkeypatch.setattr(cli_main, "_resolve_node_runtime_npm", lambda: "npm")
    monkeypatch.setattr(update_cmd_deps, "_desktop_app_present", lambda *_args: True)
    monkeypatch.setattr(cli_main, "_desktop_build_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_main,
        "_run_logged_subprocess",
        lambda command, **kwargs: (commands.append(command), SimpleNamespace(returncode=0, stdout=""))[1],
    )

    assert update_cmd_deps._rebuild_desktop_after_update(
        desktop_dir, had_desktop_app_before_update=True
    )

    assert commands[0][0] == str(dot_python)


def test_core_health_probe_runs_the_selected_interpreter(tmp_path, monkeypatch):
    dot_python, formal_python = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"prefix": str(tmp_path / ".venv")}),
            stderr="",
        )

    monkeypatch.setattr(update_cmd_deps.subprocess, "run", run)

    healthy, detail = update_cmd_deps._venv_core_imports_healthy()

    assert healthy, detail
    assert calls[0][0][0] == str(dot_python)
    assert calls[0][0][0] != str(formal_python)


def test_repair_does_not_create_a_second_venv_when_dot_venv_is_active(tmp_path, monkeypatch):
    dot_root = tmp_path / ".venv"
    dot_python = dot_root / "Scripts" / "python.exe"
    dot_python.parent.mkdir(parents=True)
    dot_python.write_bytes(b"fixture")
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    created = []

    class Result:
        returncode = 0

    monkeypatch.setattr("hermes_cli.managed_uv.ensure_uv", lambda: "uv")
    monkeypatch.setattr(cli_main, "_abort_dependency_sync_if_self_locked", lambda _resume: None)
    monkeypatch.setattr(cli_main, "_write_update_incomplete_marker", lambda: None)
    monkeypatch.setattr(cli_main, "_clear_update_incomplete_marker", lambda: None)
    monkeypatch.setattr(cli_main, "_install_python_dependencies_with_optional_fallback", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_refresh_active_lazy_features", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_restore_active_tool_dependencies", lambda *a, **k: None)
    monkeypatch.setattr(update_cmd, "_pip_install_prefix", lambda _uv: (["uv", "pip"], {"VIRTUAL_ENV": str(dot_root)}))
    monkeypatch.setattr(update_cmd, "_venv_core_imports_healthy", lambda: (True, ""))
    monkeypatch.setattr(update_cmd, "_repair_node_deps_on_current_checkout", lambda *a, **k: True)
    monkeypatch.setattr(cli_main.subprocess, "run", lambda command, **kwargs: (created.append(command), Result())[1])

    complete = update_cmd._repair_venv_on_current_checkout(
        assume_yes=True,
        gateway_mode=False,
        pre_update_snapshot_id=None,
        had_desktop_app_before_update=False,
        active_lazy_features=[],
        active_tool_dependencies=[],
        _windows_gateway_resume=None,
    )

    assert complete
    assert not any(command[:2] == ["uv", "venv"] for command in created)


def test_critical_import_probe_uses_the_running_dot_venv(tmp_path, monkeypatch):
    dot_python, formal_python = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="probe failed")

    monkeypatch.setattr(update_cmd_deps, "bounded_probe_run", run)
    update_cmd_deps._critical_module_import_failures(tmp_path)

    assert calls[0][0] == str(dot_python)
    assert calls[0][0] != str(formal_python)


def test_critical_import_probe_spawn_failure_is_unhealthy(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)

    def fail_spawn(*_args, **_kwargs):
        raise OSError("interpreter unavailable")

    monkeypatch.setattr(update_cmd_deps, "bounded_probe_run", fail_spawn)

    ok, module, error = update_cmd._validate_critical_modules_import(tmp_path)

    assert not ok
    assert module == "critical-module probe"
    assert error and "spawn" in error.lower()


def test_foreign_file_ownership_gate_checks_the_running_dot_venv(tmp_path, monkeypatch):
    dot_python, _ = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    inspected = []
    monkeypatch.setattr(
        update_cmd_deps,
        "_venv_foreign_owned_paths",
        lambda root: (inspected.append(Path(root)), [])[1],
    )

    update_cmd_deps._refuse_update_if_venv_foreign_owned(tmp_path)

    assert inspected == [tmp_path / ".venv"]


def test_post_update_sqlite_probe_uses_the_running_dot_venv(tmp_path, monkeypatch):
    dot_python, formal_python = _make_dual_layout(tmp_path)
    _patch_active_dot_venv(monkeypatch, tmp_path, dot_python)
    seen = []

    def probe(python):
        seen.append(Path(python))
        return SimpleNamespace(wal_reset_vulnerable=False, sqlite_version_string="3.45.1")

    monkeypatch.setattr("hermes_cli.sqlite_runtime.probe_sqlite_runtime", probe)

    safe, info = update_cmd_maint._post_update_sqlite_runtime_status()

    assert safe
    assert info is not None
    assert seen == [dot_python]
    assert seen[0] != formal_python
