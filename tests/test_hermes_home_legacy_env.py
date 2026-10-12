"""The legacy HERMES_HOME name keeps resolving the Stardust home.

STARDUST_HOME is the primary home variable. HERMES_HOME is the pre-rename name: an install
whose environment still carries it (the owner's user environment and desktop launcher) must keep
its data home, so HERMES_HOME is read only when STARDUST_HOME is unset or blank. Nothing here
touches the real home: Path.home() and LOCALAPPDATA point at pytest tmp directories.
"""

import sys
from pathlib import Path

import pytest

import hermes_constants
from hermes_constants import (
    display_hermes_home,
    get_default_hermes_root,
    get_hermes_home,
    get_process_hermes_home,
)


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("STARDUST_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(hermes_constants, "_profile_fallback_warned", False)
    return home


def _platform_default_home(user_home: Path, tmp_path: Path) -> Path:
    if sys.platform == "win32":
        return tmp_path / "LocalAppData" / "stardust"
    return user_home / ".stardust"


def test_legacy_home_is_used_when_stardust_home_is_unset(user_home, tmp_path, monkeypatch):
    legacy = tmp_path / "owner-home"
    monkeypatch.setenv("HERMES_HOME", str(legacy))

    assert get_hermes_home() == legacy


def test_stardust_home_wins_over_legacy_home(user_home, tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    monkeypatch.setenv("STARDUST_HOME", str(primary))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy"))

    assert get_hermes_home() == primary


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_stardust_home_counts_as_unset(user_home, tmp_path, monkeypatch, blank):
    legacy = tmp_path / "owner-home"
    monkeypatch.setenv("STARDUST_HOME", blank)
    monkeypatch.setenv("HERMES_HOME", str(legacy))

    assert get_hermes_home() == legacy


def test_neither_variable_uses_the_platform_default(user_home, tmp_path):
    assert get_hermes_home() == _platform_default_home(user_home, tmp_path)


def test_process_home_follows_the_legacy_home(user_home, tmp_path, monkeypatch):
    legacy = tmp_path / "launch-home"
    monkeypatch.setenv("HERMES_HOME", str(legacy))

    assert get_process_hermes_home() == legacy


def test_default_root_follows_a_legacy_profile_home(user_home, tmp_path, monkeypatch):
    docker_root = tmp_path / "opt" / "data"
    profile = docker_root / "profiles" / "coder"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))

    assert get_default_hermes_root() == docker_root


def test_display_renders_the_legacy_home_with_a_tilde(user_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(user_home / "data"))

    assert display_hermes_home() == "~/data"


def test_legacy_home_suppresses_the_unset_home_fallback_warning(
    user_home, tmp_path, monkeypatch, capsys
):
    default_home = _platform_default_home(user_home, tmp_path)
    default_home.mkdir(parents=True)
    (default_home / "active_profile").write_text("coder\n")
    legacy = tmp_path / "owner-home"
    monkeypatch.setenv("HERMES_HOME", str(legacy))

    assert get_hermes_home() == legacy
    assert "STARDUST_HOME fallback" not in capsys.readouterr().err


def test_mapping_lookup_reads_only_the_environment_it_is_given(monkeypatch):
    # A foreign process's environment (psutil/proc) must not be overlaid with ours.
    from hermes_constants import configured_home_text

    monkeypatch.setenv("STARDUST_HOME", "/this-process/primary")
    monkeypatch.setenv("HERMES_HOME", "/this-process/legacy")

    assert configured_home_text({"HERMES_HOME": "/other/legacy"}) == "/other/legacy"
    assert configured_home_text({"STARDUST_HOME": " /other/primary "}) == "/other/primary"
    assert configured_home_text({}) == ""


def test_legacy_home_name_is_refused_by_the_dashboard_env_writer():
    # Writable from the dashboard, the legacy name would redirect the home on the next start.
    from hermes_cli.config import validate_env_var_name_for_write

    with pytest.raises(ValueError, match="denylist"):
        validate_env_var_name_for_write("HERMES_HOME")


def test_legacy_home_name_stays_process_global_under_multiplex():
    # Process settings keep reading os.environ in multiplex mode; they are not profile secrets.
    from agent.secret_scope import _is_global_env

    assert _is_global_env("HERMES_HOME")
