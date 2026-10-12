"""Renamed desktop variables: the current name wins, and the legacy name is the fallback with one warning."""

import sys

import pytest

from renamed_env import (
    RENAMED_DESKTOP_ENV,
    read_renamed_env,
    renamed_env_is_set,
    reset_renamed_env_warnings_for_tests,
)

NAMES = sorted(RENAMED_DESKTOP_ENV)


@pytest.fixture(autouse=True)
def _fresh_warning_state():
    reset_renamed_env_warnings_for_tests()
    yield
    reset_renamed_env_warnings_for_tests()


@pytest.mark.parametrize("name", NAMES)
def test_current_name_wins_without_warning(name, capsys):
    legacy = RENAMED_DESKTOP_ENV[name]

    assert read_renamed_env(name, {name: "current", legacy: "legacy"}) == "current"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("name", NAMES)
def test_legacy_name_is_used_when_current_is_missing_with_one_warning(name, capsys):
    legacy = RENAMED_DESKTOP_ENV[name]
    env = {legacy: "legacy-value"}

    assert read_renamed_env(name, env) == "legacy-value"
    assert read_renamed_env(name, env) == "legacy-value"
    err = capsys.readouterr().err
    assert err.count("is deprecated") == 1
    assert legacy in err and name in err
    assert "legacy-value" not in err


@pytest.mark.parametrize("name", NAMES)
def test_empty_current_name_falls_back_to_legacy(name):
    legacy = RENAMED_DESKTOP_ENV[name]

    assert read_renamed_env(name, {name: "", legacy: "legacy-value"}) == "legacy-value"


@pytest.mark.parametrize("name", NAMES)
def test_neither_name_set_returns_none_silently(name, capsys):
    assert read_renamed_env(name, {}) is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("name", NAMES)
def test_presence_check_counts_either_name(name):
    legacy = RENAMED_DESKTOP_ENV[name]

    assert renamed_env_is_set(name, {legacy: ""}) is True
    assert renamed_env_is_set(name, {name: "1"}) is True
    assert renamed_env_is_set(name, {}) is False


def test_default_environment_is_os_environ(monkeypatch):
    for name, legacy in RENAMED_DESKTOP_ENV.items():
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setenv("STARDUST_DESKTOP_TERMINAL", "1")

    assert read_renamed_env("STARDUST_DESKTOP_TERMINAL") == "1"


def test_missing_stderr_skips_the_warning_without_failing(monkeypatch):
    # pythonw has no stderr; the legacy value must still be honoured.
    monkeypatch.setattr(sys, "stderr", None)

    assert read_renamed_env("STARDUST_DESKTOP_TERMINAL", {"HERMES_DESKTOP_TERMINAL": "1"}) == "1"
