"""The venv folder of an install root is ``venv`` or ``.venv``, chosen by the interpreter it holds.

These are the behaviour contracts behind every update-path lookup: the rule (``venv`` with an
interpreter, else ``.venv`` with one, else ``venv``), the alias that exposes a ``.venv``-only install
under ``venv``, and the unchanged answer of ``project_venv_dir`` for half-repaired folders.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import hermes_constants
from hermes_cli import venv_layout
from hermes_cli.venv_layout import ensure_venv_alias, resolve_venv_dir


def _interpreter(venv_dir: Path, *, windows: bool) -> Path:
    """Create the interpreter the rule looks for and return it."""
    python = venv_dir / ("Scripts/python.exe" if windows else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"interpreter")
    return python


# ── resolve_venv_dir ───────────────────────────────────────────────────────


def test_resolver_picks_venv_when_it_holds_the_interpreter(tmp_path):
    _interpreter(tmp_path / "venv", windows=True)

    assert resolve_venv_dir(tmp_path, windows=True) == tmp_path / "venv"


def test_resolver_picks_dotvenv_when_only_it_holds_the_interpreter(tmp_path):
    _interpreter(tmp_path / ".venv", windows=True)

    assert resolve_venv_dir(tmp_path, windows=True) == tmp_path / ".venv"


def test_resolver_prefers_venv_when_both_hold_an_interpreter(tmp_path):
    _interpreter(tmp_path / "venv", windows=True)
    _interpreter(tmp_path / ".venv", windows=True)

    assert resolve_venv_dir(tmp_path, windows=True) == tmp_path / "venv"


def test_resolver_defaults_to_venv_when_neither_holds_an_interpreter(tmp_path):
    # A fresh install, or a folder mid-repair with no interpreter yet: the installers' name wins.
    (tmp_path / ".venv").mkdir()

    assert resolve_venv_dir(tmp_path, windows=True) == tmp_path / "venv"


def test_resolver_skips_a_half_repaired_venv_for_a_working_dotvenv(tmp_path):
    (tmp_path / "venv" / "Scripts").mkdir(parents=True)  # directory without an interpreter
    _interpreter(tmp_path / ".venv", windows=True)

    assert resolve_venv_dir(tmp_path, windows=True) == tmp_path / ".venv"


def test_resolver_uses_the_posix_interpreter_layout_off_windows(tmp_path):
    _interpreter(tmp_path / ".venv", windows=False)
    # A Windows-layout interpreter is not the POSIX one: the POSIX rule must not accept it.
    _interpreter(tmp_path / "venv", windows=True)

    assert resolve_venv_dir(tmp_path, windows=False) == tmp_path / ".venv"


def test_resolver_follows_the_host_layout_by_default(tmp_path):
    _interpreter(tmp_path / ".venv", windows=os.name == "nt")

    assert resolve_venv_dir(tmp_path) == tmp_path / ".venv"


# ── project_venv_dir (delegates to the resolver, keeps its existence fallback) ──


def test_project_venv_dir_returns_the_resolver_pick_over_a_broken_venv(tmp_path):
    _interpreter(tmp_path / ".venv", windows=os.name == "nt")
    (tmp_path / "venv" / "lib").mkdir(parents=True)  # venv exists but its interpreter is gone

    assert hermes_constants.project_venv_dir(tmp_path) == tmp_path / ".venv"


def test_project_venv_dir_keeps_a_bare_dotvenv_directory_as_present(tmp_path):
    (tmp_path / ".venv").mkdir()

    assert hermes_constants.project_venv_dir(tmp_path) == tmp_path / ".venv"


def test_project_venv_dir_is_none_without_any_venv_directory(tmp_path):
    assert hermes_constants.project_venv_dir(tmp_path) is None


# ── ensure_venv_alias ──────────────────────────────────────────────────────


@pytest.mark.windows_only
def test_alias_junction_exposes_dotvenv_under_venv_on_windows(tmp_path):
    python = _interpreter(tmp_path / ".venv", windows=True)
    alias = tmp_path / "venv"
    try:
        assert ensure_venv_alias(tmp_path, windows=True) == "created"

        assert resolve_venv_dir(tmp_path, windows=True) == alias
        assert (alias / "Scripts" / "python.exe").read_bytes() == python.read_bytes()
        assert ensure_venv_alias(tmp_path, windows=True) == "present"  # idempotent
    finally:
        if os.path.lexists(alias):
            os.rmdir(alias)  # removes the junction only; .venv is never followed


@pytest.mark.linux_only
def test_alias_symlink_exposes_dotvenv_under_venv_on_posix(tmp_path):
    _interpreter(tmp_path / ".venv", windows=False)

    assert ensure_venv_alias(tmp_path, windows=False) == "created"

    assert os.readlink(tmp_path / "venv") == ".venv"
    assert resolve_venv_dir(tmp_path, windows=False) == tmp_path / "venv"
    assert ensure_venv_alias(tmp_path, windows=False) == "present"


def test_alias_never_touches_an_existing_venv_directory(tmp_path):
    _interpreter(tmp_path / ".venv", windows=os.name == "nt")
    _interpreter(tmp_path / "venv", windows=os.name == "nt")
    keeper = tmp_path / "venv" / "keep.txt"
    keeper.write_text("real venv")

    assert ensure_venv_alias(tmp_path, windows=os.name == "nt") == "present"

    assert keeper.read_text() == "real venv"
    assert not (tmp_path / "venv").is_symlink()


def test_alias_never_replaces_an_existing_venv_file(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "venv").write_text("placeholder")

    assert ensure_venv_alias(tmp_path, windows=False) == "present"
    assert (tmp_path / "venv").read_text() == "placeholder"


def test_alias_does_nothing_without_a_dotvenv_directory(tmp_path):
    assert ensure_venv_alias(tmp_path, windows=False) == "no-dotvenv"
    assert not os.path.lexists(tmp_path / "venv")


def test_alias_failure_is_reported_and_never_raised(tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir()

    def refuse(*_args, **_kwargs):
        raise OSError("symlink privilege missing")

    monkeypatch.setattr(venv_layout.os, "symlink", refuse)

    assert ensure_venv_alias(tmp_path, windows=False) == "failed"
    assert not os.path.lexists(tmp_path / "venv")
    assert (tmp_path / ".venv").is_dir()  # the failed alias leaves .venv untouched
