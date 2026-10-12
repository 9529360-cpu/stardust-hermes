"""The venv folder of an install root is ``venv`` or ``.venv``, chosen by the interpreter it holds.

These are the behaviour contracts behind every update-path lookup: the rule (``venv`` with an
interpreter, else ``.venv`` with one, else ``venv``), the alias that exposes a ``.venv``-only install
under ``venv``, the unchanged answer of ``project_venv_dir`` for half-repaired folders, and the names every
updater, installer and repair path repeats agreeing with ``venv_layout``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import hermes_constants
from hermes_cli import managed_uv, venv_layout
from hermes_cli.venv_layout import ensure_venv_alias, resolve_venv_dir, venv_dirs_to_guard


def _interpreter(venv_dir: Path, *, windows: bool) -> Path:
    """Create the interpreter the rule looks for and return it."""
    python = venv_dir / ("Scripts/python.exe" if windows else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"interpreter")
    return python


# ── resolve_venv_dir ───────────────────────────────────────────────────────


def _write_windows_interpreter(folder: Path) -> None:
    (folder / "Scripts").mkdir(parents=True)
    (folder / "Scripts" / "python.exe").write_text("", encoding="utf-8")


def test_guard_folders_cover_both_layouts_when_both_hold_an_interpreter(tmp_path):
    _write_windows_interpreter(tmp_path / "venv")
    _write_windows_interpreter(tmp_path / ".venv")

    assert venv_dirs_to_guard(tmp_path, windows=True) == [tmp_path / "venv", tmp_path / ".venv"]


def test_guard_folders_skip_a_folder_without_an_interpreter(tmp_path):
    _write_windows_interpreter(tmp_path / ".venv")
    (tmp_path / "venv").mkdir()

    assert venv_dirs_to_guard(tmp_path, windows=True) == [tmp_path / ".venv"]


def test_guard_folders_fall_back_to_the_fresh_install_folder(tmp_path):
    assert venv_dirs_to_guard(tmp_path, windows=True) == [tmp_path / "venv"]


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


# ── one definition of the venv names, read back from every place that repeats them ──

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")


def _installer_pin_names() -> tuple[str, ...]:
    """Distinct venv folder names that scripts/install.ps1 pins UV_PROJECT_ENVIRONMENT to."""
    pins = [line for line in _source("scripts/install.ps1").splitlines() if re.search(r"UV_PROJECT_ENVIRONMENT\s*=", line)]
    assert pins, "scripts/install.ps1 no longer pins UV_PROJECT_ENVIRONMENT"
    names = []
    for line in pins:
        found = re.findall(r"(?<!\w)(\.?venv)(?!\w)", line.split("=", 1)[1])
        assert len(found) == 1, f"expected one venv folder in the pin: {line.strip()}"
        names.append(found[0])
    return tuple(dict.fromkeys(names))


def _electron_updater_names() -> tuple[str, str]:
    """(DEFAULT_VENV_NAME, UV_VENV_NAME) as apps/desktop/electron/venv-layout.ts declares them."""
    consts = dict(re.findall(r"export const (\w+) = ['\"]([^'\"]+)['\"]", _source("apps/desktop/electron/venv-layout.ts")))
    return consts["DEFAULT_VENV_NAME"], consts["UV_VENV_NAME"]


def _windows_resolver_names() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(candidate folders in order, fresh-install default) of Resolve-VenvDir in scripts/desktop-update/windows.ps1."""
    text = _source("scripts/desktop-update/windows.ps1")
    function = re.search(r"^function Resolve-VenvDir\(\[string\]\$Root\) \{\n(.*?)^\}", text, re.M | re.S)
    assert function, "Resolve-VenvDir not found in scripts/desktop-update/windows.ps1"
    body = function.group(1)
    candidates = re.search(r"foreach \(\$name in @\(([^)]*)\)\)", body)
    assert candidates, "Resolve-VenvDir no longer iterates a literal list of venv folder names"
    return (
        tuple(re.findall(r'"([^"]+)"', candidates.group(1))),
        tuple(re.findall(r'Join-Path \$Root "([^"]+)"', body)),
    )


def test_venv_names_agree_across_installer_updater_and_repair():
    """One rule names the venv folders: ``venv`` first, then ``.venv`` as the uv fallback.

    venv_layout holds the definition. The installer's UV_PROJECT_ENVIRONMENT pin, the Electron updater, the Windows
    hand-off resolver and the runtime repair list each repeat it. Changing any one of them alone fails this test.
    """
    candidates, fresh_install_default = _windows_resolver_names()
    observed = {
        "hermes_cli/venv_layout.py": (venv_layout.DEFAULT_VENV_NAME, venv_layout.UV_VENV_NAME),
        "hermes_cli/managed_uv.py": (managed_uv._VENV_NAME, managed_uv._ALT_VENV_NAME),
        "apps/desktop/electron/venv-layout.ts": _electron_updater_names(),
        "scripts/desktop-update/windows.ps1 candidates": candidates,
        "scripts/desktop-update/windows.ps1 fresh-install default": fresh_install_default,
        "scripts/install.ps1 UV_PROJECT_ENVIRONMENT pins": _installer_pin_names(),
    }
    assert observed == {
        "hermes_cli/venv_layout.py": ("venv", ".venv"),
        "hermes_cli/managed_uv.py": ("venv", ".venv"),
        "apps/desktop/electron/venv-layout.ts": ("venv", ".venv"),
        "scripts/desktop-update/windows.ps1 candidates": ("venv", ".venv"),
        "scripts/desktop-update/windows.ps1 fresh-install default": ("venv",),
        "scripts/install.ps1 UV_PROJECT_ENVIRONMENT pins": ("venv",),
    }
