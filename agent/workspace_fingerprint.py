"""Content fingerprint of a git workspace, used to tell a *possible* change from a real one.

The verification ledger marks evidence stale on intent (a custom script, ``python -c`` that starts a
process, ...) because a shell result is not a filesystem diff. A fingerprint taken when a check passes,
compared with one taken when the stop gate asks, proves the tree is byte-identical, so a read-only
command flagged conservatively does not demand the same tests again. ``None`` whenever the workspace
cannot be fingerprinted cheaply (not git, git missing, too many/large untracked files, timeout): callers
then keep the conservative verdict."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

_GIT_TIMEOUT_SECONDS = 10
_MAX_UNTRACKED_FILES = 2000
_MAX_UNTRACKED_BYTES = 64 * 1024 * 1024


def _git(root: Path, *args: str) -> Optional[bytes]:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def workspace_content_fingerprint(root: str | Path | None) -> Optional[str]:
    """sha256 over HEAD, the tracked diff against HEAD (binary) and every untracked, non-ignored file."""
    if not root:
        return None
    path = Path(root)
    top = _git(path, "rev-parse", "--show-toplevel")
    if top is None or Path(top.decode("utf-8").strip()).resolve() != path.resolve():
        return None
    head = _git(path, "rev-parse", "--verify", "-q", "HEAD")
    diff = _git(path, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-color")
    untracked = _git(path, "ls-files", "--others", "--exclude-standard", "-z")
    if head is None or diff is None or untracked is None:
        return None
    names = sorted(n for n in untracked.split(b"\0") if n)
    if len(names) > _MAX_UNTRACKED_FILES:
        return None
    digest = hashlib.sha256()
    digest.update(head.strip() + b"\0" + diff + b"\0")
    total = 0
    for name in names:
        file_path = path / name.decode("utf-8", "surrogateescape")
        try:
            data = file_path.read_bytes() if file_path.is_file() else b"[non-file]"
        except OSError:
            return None
        total += len(data)
        if total > _MAX_UNTRACKED_BYTES:
            return None
        digest.update(name + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


__all__ = ["workspace_content_fingerprint"]
