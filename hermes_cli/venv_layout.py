"""Venv folder layout of a Stardust install root: ``venv`` or ``.venv``.

``uv venv`` defaults to ``.venv`` while the installers create ``venv``, so both layouts exist in the wild.
One rule decides which folder an install uses, shared with the Electron updater
(``apps/desktop/electron/venv-layout.ts``) and the Windows hand-off script
(``scripts/desktop-update/windows.ps1``, ``Resolve-VenvDir``). Keep the three in lockstep.

Stdlib-only (``hermes_constants`` is stdlib-only too): imported from the early-recovery path that must still
run when the venv is corrupted.
"""

from __future__ import annotations

import contextlib
import os
import stat
import subprocess
import sys
from pathlib import Path

from hermes_constants import venv_python_path

#: The fresh-install folder and the ``uv venv`` default, in the order the rule consults them.
DEFAULT_VENV_NAME = "venv"
UV_VENV_NAME = ".venv"


def resolve_venv_dir(root, *, windows: bool | None = None) -> Path:
    """The venv folder of *root* (a ``Path``; joined with ``/`` so path-like test doubles keep working).

    ``<root>/venv`` when it holds the interpreter, else ``<root>/.venv`` when that does, else
    ``<root>/venv`` (the fresh-install default). The interpreter is ``Scripts/python.exe`` on Windows and
    ``bin/python`` elsewhere; *windows* lets tests exercise the other layout.
    """
    for name in (DEFAULT_VENV_NAME, UV_VENV_NAME):
        candidate = root / name
        if venv_python_path(candidate, windows=windows).is_file():
            return candidate
    return root / DEFAULT_VENV_NAME


def ensure_venv_alias(root, *, windows: bool | None = None) -> str:
    """Expose a ``.venv``-only install under ``venv`` too (junction on Windows, symlink on POSIX).

    Tools that still hardcode ``venv`` keep working. Idempotent and conservative: ``venv`` is created only
    when no entry of that name exists at all (a directory, junction, file or dangling link is left untouched),
    and ``.venv`` is never modified. Never raises; a failed alias is reported and the resolver still finds
    ``.venv``. Returns ``"created"``, ``"present"``, ``"no-dotvenv"`` or ``"failed"``.
    """
    if windows is None:
        windows = sys.platform == "win32"
    alias = root / DEFAULT_VENV_NAME
    target = root / UV_VENV_NAME
    if os.path.lexists(alias):
        return "present"
    if not target.is_dir():
        return "no-dotvenv"
    try:
        if windows:
            # mklink /J needs no elevation, unlike os.symlink on Windows.
            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                check=True,
                capture_output=True,
            )
        else:
            os.symlink(UV_VENV_NAME, alias, target_is_directory=True)
    except (OSError, subprocess.SubprocessError) as exc:
        with contextlib.suppress(OSError, ValueError):
            print(f"  ⚠ Could not create the venv alias ({exc}); continuing with .venv", file=sys.stderr)
        return "failed"
    return "created"


_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_link_entry(path) -> bool:
    """True for a symlink or a Windows reparse point (a directory junction). Never follows *path*."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def unlink_link_entry(path) -> None:
    """Remove a link entry itself, never its target: ``os.rmdir`` for a Windows directory junction, else ``os.unlink``."""
    st = os.lstat(path)
    if sys.platform == "win32" and getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_DIRECTORY:
        os.rmdir(path)
    else:
        os.unlink(path)


def unlink_venv_aliases(tree) -> list[Path]:
    """Remove the venv alias of *tree* and of each direct child folder (where install roots live). Only link
    entries go, with ``os.rmdir`` (junction) or ``os.unlink`` (symlink); a real venv folder is never touched, so a
    recursive delete of *tree* that runs next finds the real ``.venv`` as an ordinary folder. Returns what it removed.
    """
    base = Path(tree)
    candidates = [base / DEFAULT_VENV_NAME]
    try:
        children = [child for child in base.iterdir() if child.is_dir() and not is_link_entry(child)]
    except OSError:
        children = []
    candidates.extend(child / DEFAULT_VENV_NAME for child in children)
    removed: list[Path] = []
    for alias in candidates:
        if is_link_entry(alias):
            unlink_link_entry(alias)
            removed.append(alias)
    return removed
