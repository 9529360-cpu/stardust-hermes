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


def _link_target_is_inside(link: Path, base: Path) -> bool:
    """True when the link's target lies inside *base*; a target that cannot be resolved counts as inside (fail closed)."""
    try:
        target = link.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    return _path_is_under(base, target)


def _refuse_link_chain(policy: ManagedFilesPolicy, candidate: Path, *, leaf_link_ok: bool) -> None:
    """403 when a mutation would reach the managed tree through a link (the venv alias is a junction to ``.venv``).

    Walks the lexical path of *candidate* from its anchor before anything is resolved, because resolving would follow
    the link and hide it. A link below the managed base is refused, and so is a link anywhere whose target lies inside
    the base, which catches a path that enters the tree from outside. The final component may be a link only when
    *leaf_link_ok*: a delete then unlinks that link as itself. Only symlinks and junctions count as links.
    """
    base = policy.locked_root if policy.locked_root is not None else policy.default_path
    parts = candidate.parts
    current = Path(candidate.anchor)
    for index, part in enumerate(parts[1:]):
        current = current / part
        is_last = index == len(parts) - 2
        if not is_link_entry(current) or (leaf_link_ok and is_last):
            continue
        if _path_is_under(base, current) or _link_target_is_inside(current, base):
            raise HTTPException(status_code=403, detail="Path goes through a link")


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
    mutating: bool = False,
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

    if mutating:
        _refuse_link_chain(policy, candidate, leaf_link_ok=keep_link_leaf)

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
