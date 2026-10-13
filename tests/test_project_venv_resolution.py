from pathlib import Path

from hermes_constants import probe_project_venv, resolve_project_venv


def _make_env(root: Path, name: str, *, windows: bool) -> Path:
    bin_dir = root / name / ("Scripts" if windows else "bin")
    bin_dir.mkdir(parents=True)
    python = bin_dir / ("python.exe" if windows else "python")
    python.write_text("fixture", encoding="utf-8")
    launcher = bin_dir / ("stardust.exe" if windows else "stardust")
    launcher.write_text("fixture", encoding="utf-8")
    return python


def test_resolves_single_dot_venv_as_development_environment(tmp_path):
    python = _make_env(tmp_path, ".venv", windows=True)

    result = resolve_project_venv(tmp_path, windows=True, runtime_mode="development")

    assert result.venv_dir == tmp_path / ".venv"
    assert result.python == python
    assert result.launcher == python.parent / "stardust.exe"
    assert result.environment_type == "development"
    assert result.error is None


def test_preserves_single_managed_venv_layout(tmp_path):
    python = _make_env(tmp_path, "venv", windows=True)

    result = resolve_project_venv(tmp_path, windows=True, runtime_mode="managed")

    assert result.venv_dir == tmp_path / "venv"
    assert result.python == python
    assert result.environment_type == "managed"
    assert result.error is None


def test_runtime_python_disambiguates_two_environments(tmp_path):
    _make_env(tmp_path, ".venv", windows=True)
    managed_python = _make_env(tmp_path, "venv", windows=True)

    result = resolve_project_venv(
        tmp_path,
        runtime_python=str(managed_python),
        windows=True,
        runtime_mode="development",
    )

    assert result.venv_dir == tmp_path / "venv"
    assert result.python == managed_python
    assert result.error is None


def test_ambiguous_dual_environment_fails_closed_without_runtime_identity(tmp_path):
    _make_env(tmp_path, ".venv", windows=True)
    _make_env(tmp_path, "venv", windows=True)

    result = resolve_project_venv(tmp_path, windows=True, runtime_mode="unknown")

    assert result.venv_dir is None
    assert result.python is None
    assert "ambiguous" in result.error.lower()


def test_installation_source_disambiguates_dual_environments_when_runtime_python_is_external(tmp_path):
    _make_env(tmp_path, ".venv", windows=True)
    managed_python = _make_env(tmp_path, "venv", windows=True)

    result = resolve_project_venv(
        tmp_path,
        runtime_python=r"C:\\Python\\python.exe",
        installation_source="managed",
        runtime_mode="managed",
        windows=True,
    )

    assert result.venv_dir == tmp_path / "venv"
    assert result.python == managed_python


def test_virtual_env_disambiguates_dual_environments(tmp_path):
    _make_env(tmp_path, ".venv", windows=True)
    development_python = _make_env(tmp_path, "venv", windows=True)

    result = resolve_project_venv(
        tmp_path,
        virtual_env=str(tmp_path / "venv"),
        windows=True,
        runtime_mode="development",
    )

    assert result.venv_dir == tmp_path / "venv"
    assert result.python == development_python


def test_missing_or_interpreterless_environment_is_not_healthy(tmp_path):
    (tmp_path / ".venv").mkdir()

    result = resolve_project_venv(tmp_path, windows=True, runtime_mode="development")

    assert result.venv_dir is None
    assert result.python is None
    assert result.error


def test_probe_does_not_call_a_present_but_invalid_python_healthy(tmp_path):
    _make_env(tmp_path, ".venv", windows=True)
    result = resolve_project_venv(tmp_path, windows=True, runtime_mode="development")

    healthy, detail = probe_project_venv(result)

    assert not healthy
    assert detail
