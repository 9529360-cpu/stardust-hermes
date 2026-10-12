"""Managed-files policy for the dashboard file browser: root resolution, path containment, entry metadata.
"""

import mimetypes
import os
import urllib.request
from dataclasses import dataclass
from fastapi import HTTPException, Request
from pathlib import Path
from typing import Any, Dict

from hermes_cli.venv_layout import is_link_entry


_MANAGED_FILES_ROOT_ENV = "STARDUST_DASHBOARD_FILES_ROOT"
_HOSTED_MANAGED_FILES_ROOT = Path("/opt/data")


@dataclass(frozen=True)
class ManagedFilesPolicy:
    default_path: Path
    locked_root: Path | None
    can_change_path: bool


def _fs_lexical_path(raw_path: str, *, cwd: str | None = None) -> Path:
    """The absolute path *raw_path* names, not resolved, so a link in it stays visible (see ``_fs_path``)."""
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Path is required")
    if "\0" in raw:
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        if raw.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(raw)
            uri_path = parsed.path
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                if os.name != "nt":
                    raise ValueError
                uri_path = f"//{parsed.netloc}{uri_path}"
            raw = urllib.request.url2pathname(uri_path)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            base = Path(cwd).expanduser() if cwd is not None else Path.cwd()
            if not base.is_absolute():
                raise HTTPException(status_code=400, detail="Session working directory is unavailable")
            candidate = base / candidate
        return candidate
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _fs_path(raw_path: str, *, cwd: str | None = None) -> Path:
    candidate = _fs_lexical_path(raw_path, cwd=cwd)
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _canonical_path(path: Path, *, require_exists: bool = False) -> Path:
    try:
        return path.expanduser().resolve(strict=require_exists)
    except FileNotFoundError:
        if require_exists:
            raise HTTPException(status_code=404, detail="Path not found")
        raise
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _ensure_managed_root(raw_path: str | Path) -> Path:
    root = Path(raw_path).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"Managed files root is unavailable: {exc}")
    if not resolved.is_dir():
        raise HTTPException(status_code=500, detail="Managed files root is not a directory")
    return resolved


def _path_is_under(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _install_root() -> Path:
    """The checkout this dashboard runs from: the root the update and the holder guard manage (``PROJECT_ROOT``)."""
    from hermes_cli import main as _main  # late: hermes_cli.main imports the web server, not the other way round

    return Path(_main.PROJECT_ROOT)


_VENV_FOLDER_NAMES = frozenset({"venv", ".venv"})


def _inside_venv_folder(path: Path, root: Path, anchors: list[Path]) -> bool:
    """True when *path* is a venv folder under the install root, or lies inside one.

    A venv folder is recognised by name, ``venv`` or ``.venv``, with or without an interpreter. *anchors* are the
    resolved venv folders of the install, so a path that reaches one through a link is caught as well.
    """
    if any(path == anchor or anchor in path.parents for anchor in anchors):
        return True
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part in _VENV_FOLDER_NAMES for part in relative.parts)


def _subtree_has_venv_folder(folder: Path) -> bool:
    """True when a venv folder name appears below *folder*. Links are not followed; a walk error counts as found."""
    errors: list[OSError] = []
    for _dirpath, dirnames, _filenames in os.walk(folder, onerror=errors.append):
        if any(name in _VENV_FOLDER_NAMES for name in dirnames):
            return True
    return bool(errors)


def _refuse_venv_mutation(candidate: Path, *, mutation: str, recursive: bool = False) -> None:
    """403 when a file mutation would touch a venv folder of the install, by name, with or without links.

    The dashboard never mutates anything inside a venv or ``.venv`` folder under the install root. *candidate* is the
    path as addressed. The check covers the path as addressed, the entry with its ancestors resolved, and the fully
    resolved target. One exception: deleting the alias itself, a final link named ``venv`` directly in the install
    root. That link is unlinked and its target is left alone. A recursive delete is also refused when the folder
    contains the install root, or holds a venv folder.
    """
    root = _install_root().resolve(strict=False)
    anchors = [(root / name).resolve(strict=False) for name in ("venv", ".venv")]
    try:
        entry = candidate.parent.resolve(strict=False) / candidate.name
        if mutation == "delete" and entry == root / "venv" and is_link_entry(entry):
            return
        target = entry.resolve(strict=False)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=403, detail="Path cannot be resolved")
    for location in (candidate, entry, target):
        if _inside_venv_folder(location, root, anchors):
            raise HTTPException(status_code=403, detail="Path is inside a venv folder")
    if mutation == "delete" and recursive and not is_link_entry(entry) and os.path.isdir(entry):
        if entry == root or entry in root.parents:
            raise HTTPException(status_code=403, detail="Path contains the install root")
        if root in entry.parents and _subtree_has_venv_folder(entry):
            raise HTTPException(status_code=403, detail="Path contains a venv folder")


def _path_text(raw_path: str | None) -> str:
    text = str(raw_path or "").strip()
    if "\x00" in text:
        raise HTTPException(status_code=400, detail="Invalid path")
    return text


def _default_hermes_root_is_opt_data() -> bool:
    from hermes_constants import configured_home_text

    raw = configured_home_text()
    if not raw:
        return False
    try:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root().expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        root = Path(raw).expanduser().resolve(strict=False)
    return root == _HOSTED_MANAGED_FILES_ROOT


def _dashboard_local_update_managed_externally() -> bool:
    """Stardust never offers an in-dashboard backend source update.

    The personal edition is maintained from its own pinned Stardust source authority.  The inherited
    Hermes dashboard updater still has an upstream-oriented check/apply state machine; exposing it
    would let a manual ``Check now`` bypass Stardust's passive-update opt-out and then offer an
    apply action whose CLI entrypoint is intentionally disabled.  Treat the backend as externally
    managed on every deployment kind until Stardust owns a complete release/update/rollback lane.

    This policy covers the backend/source checkout only.  It does not disable the desktop shell's
    own client-version/build lifecycle.
    """
    return True


def _managed_files_policy(request: Request, *, create_root: bool = True) -> ManagedFilesPolicy:
    raw_forced_root = os.environ.get(_MANAGED_FILES_ROOT_ENV, "").strip()
    if raw_forced_root:
        root = _ensure_managed_root(raw_forced_root) if create_root else _canonical_path(Path(raw_forced_root))
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    # Remote/OAuth access does not imply a hosted container (a gated macOS launchd
    # install still browses its home). Lock to /opt/data only when the Hermes
    # root actually IS /opt/data or STARDUST_DASHBOARD_FILES_ROOT is set.
    if _default_hermes_root_is_opt_data():
        root = _ensure_managed_root(_HOSTED_MANAGED_FILES_ROOT) if create_root else _HOSTED_MANAGED_FILES_ROOT
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    home = _canonical_path(Path.home())
    return ManagedFilesPolicy(default_path=home, locked_root=None, can_change_path=True)


def _resolve_managed_path(
    raw_path: str | None,
    request: Request,
    *,
    for_write: bool = False,
    keep_link_leaf: bool = False,
    mutation: str | None = None,
    recursive: bool = False,
) -> tuple[ManagedFilesPolicy, Path, str]:
    policy = _managed_files_policy(request)
    text = _path_text(raw_path)
    root = policy.locked_root

    if root is not None and (not text or text in {".", "/"}):
        candidate = root
    elif not text:
        candidate = policy.default_path
    else:
        candidate = Path(text).expanduser()
        if root is not None and not candidate.is_absolute():
            if any(part == ".." for part in candidate.parts):
                raise HTTPException(status_code=400, detail="Path cannot contain '..'")
            candidate = root / candidate
        elif not candidate.is_absolute():
            raise HTTPException(status_code=400, detail="Path must be absolute")

    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="Path cannot contain '..'")

    if mutation is not None:
        _refuse_venv_mutation(candidate, mutation=mutation, recursive=recursive)

    if for_write and not candidate.exists():
        parent = _canonical_path(candidate.parent)
        resolved = parent / candidate.name
    elif keep_link_leaf and is_link_entry(candidate):
        # A link is removed as itself: resolve its parent, keep the leaf, never follow it (the venv alias).
        resolved = _canonical_path(candidate.parent, require_exists=True) / candidate.name
    else:
        resolved = _canonical_path(candidate, require_exists=not for_write)

    if root is not None and not _path_is_under(root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    return policy, resolved, str(resolved)


def _managed_response_meta(policy: ManagedFilesPolicy) -> Dict[str, Any]:
    locked_root = str(policy.locked_root) if policy.locked_root is not None else None
    return {"root": locked_root, "locked_root": locked_root, "can_change_path": policy.can_change_path}


def _managed_link_entry(target: Path) -> Dict[str, Any]:
    """A symlink or junction is listed as itself: its own path, never resolved (see ``is_link_entry``).

    Resolving the venv alias produced the real ``.venv`` path, so picking the alias in the Files page deleted the
    real venv. A dangling link (its target is gone) is listed with ``broken`` instead of failing the listing.
    """
    try:
        st = os.lstat(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not stat path: {exc}")
    broken = not os.path.exists(target)
    is_dir = not broken and os.path.isdir(target)
    return {
        "name": target.name,
        "path": str(target),
        "is_directory": is_dir,
        "is_link": True,
        "broken": broken,
        "size": None,
        "mtime": st.st_mtime,
        "mime_type": None if is_dir else (mimetypes.guess_type(target.name)[0] or "application/octet-stream"),
    }


def _managed_file_entry(policy: ManagedFilesPolicy, target: Path) -> Dict[str, Any]:
    if is_link_entry(target):
        return _managed_link_entry(target)
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")
    if policy.locked_root is not None and not _path_is_under(policy.locked_root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    try:
        st = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not stat path: {exc}")

    is_dir = resolved.is_dir()
    mime_type = None if is_dir else (mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
    return {
        "name": target.name or resolved.name or str(resolved),
        "path": str(resolved),
        "is_directory": is_dir,
        "size": None if is_dir else st.st_size,
        "mtime": st.st_mtime,
        "mime_type": mime_type,
    }
