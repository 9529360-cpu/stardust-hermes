"""Private local diagnostics export for Stardust support handoff.

The Desktop support flow must not silently choose a third-party upload authority.
This module builds a force-redacted, short-lived ZIP on the backend host. Desktop
then downloads it through the existing authenticated file bridge and asks us to
discard the backend copy after the save attempt.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import stat
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Mapping

from hermes_constants import get_hermes_home

_EXPORT_PREFIX = "stardust-diagnostics-"
_EXPORT_SUFFIX = ".zip"
_EXPORT_RETENTION_SECONDS = 24 * 60 * 60
_MAX_EXPORTS = 8
_MAX_EXTRA_FILES = 4
_MAX_CLIENT_TEXT_CHARS = 524_288
_MAX_ERROR_CONTEXT_CHARS = 8_000


def _safe_client_label(label: str) -> str:
    """Return one bounded ZIP-safe label; empty means reject."""
    safe = "".join(ch for ch in str(label or "") if ch.isalnum() or ch in "._- ()").strip()[:64]
    while ".." in safe:
        safe = safe.replace("..", ".")
    return safe.lstrip(".").strip()


def _diagnostics_dir(*, create: bool = True) -> Path:
    root = get_hermes_home() / "cache" / "diagnostics"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(root, 0o700)
    return root


def _is_export_name(name: str) -> bool:
    return name.startswith(_EXPORT_PREFIX) and name.endswith(_EXPORT_SUFFIX)


def _prune_exports(root: Path) -> None:
    """Best-effort bound for crash leftovers; active Desktop saves discard eagerly."""
    if not root.is_dir():
        return
    now = time.time()
    archives: list[tuple[float, Path]] = []
    for path in root.iterdir():
        try:
            st = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            continue
        if _is_export_name(path.name):
            archives.append((st.st_mtime, path))
        elif path.name.startswith(f".{_EXPORT_PREFIX}") and path.name.endswith(".tmp"):
            if now - st.st_mtime > 60 * 60:
                with suppress(OSError):
                    path.unlink()

    archives.sort(key=lambda item: item[0], reverse=True)
    for index, (mtime, path) in enumerate(archives):
        if index >= _MAX_EXPORTS or now - mtime > _EXPORT_RETENTION_SECONDS:
            with suppress(OSError):
                path.unlink()


def _zip_text(archive: zipfile.ZipFile, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = time.localtime()[:6]
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    archive.writestr(info, text.encode("utf-8"))


def prepare_diagnostics_bundle(
    *,
    error_context: str | None = None,
    extra_files: Mapping[str, str] | None = None,
    log_lines: int | None = None,
) -> dict[str, object]:
    """Create one force-redacted temporary ZIP and return its backend-local path."""
    from hermes_cli.debug import _redact_log_text, collect_share_bundle

    lines = log_lines if isinstance(log_lines, int) and 10 <= log_lines <= 2000 else 200
    root = _diagnostics_dir(create=True)
    _prune_exports(root)

    collected = collect_share_bundle(log_lines=lines, redact=True)
    # Treat this module as its own privacy boundary. The collector currently
    # redacts backend logs already, but re-run the upload-safe redactor over
    # every text entry so a future collector change cannot silently weaken the
    # local-export contract.
    bundle = {
        str(label): _redact_log_text(str(body))
        for label, body in collected.items()
    }
    if isinstance(error_context, str) and error_context.strip():
        bundle["error-context.txt"] = _redact_log_text(
            error_context.strip()[:_MAX_ERROR_CONTEXT_CHARS]
        )

    if isinstance(extra_files, Mapping):
        for raw_label, raw_text in list(extra_files.items())[:_MAX_EXTRA_FILES]:
            if not isinstance(raw_label, str) or not isinstance(raw_text, str):
                continue
            label = _safe_client_label(raw_label)
            if label and raw_text.strip():
                bundle[f"client/{label}"] = _redact_log_text(
                    raw_text[:_MAX_CLIENT_TEXT_CHARS]
                )

    created = _dt.datetime.now(_dt.timezone.utc)
    filename = (
        f"{_EXPORT_PREFIX}{created.strftime('%Y%m%d-%H%M%S')}-"
        f"{secrets.token_hex(4)}{_EXPORT_SUFFIX}"
    )
    final_path = root / filename
    temp_path = root / f".{filename}.{secrets.token_hex(4)}.tmp"

    manifest = {
        "format": "stardust-diagnostics-v1",
        "created": created.isoformat(),
        "redacted": True,
        "temporary_backend_copy": True,
    }

    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _zip_text(
                archive,
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            for label, body in bundle.items():
                if label == "report":
                    name = "report.txt"
                else:
                    name = _safe_client_label(label)
                    if not name or name == "manifest.json":
                        continue
                _zip_text(archive, name, str(body))
        with suppress(OSError):
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, final_path)
        with suppress(OSError):
            os.chmod(final_path, 0o600)
    except Exception:
        with suppress(OSError):
            temp_path.unlink()
        with suppress(OSError):
            final_path.unlink()
        raise

    _prune_exports(root)
    return {
        "path": str(final_path),
        "filename": filename,
        "byte_size": final_path.stat().st_size,
    }


def discard_diagnostics_bundle(raw_path: str) -> bool:
    """Idempotently remove only a bundle created in this profile's diagnostics cache."""
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("diagnostics bundle path is required")

    candidate = Path(text).expanduser()
    if not candidate.is_absolute() or not _is_export_name(candidate.name):
        raise ValueError("not a Stardust diagnostics bundle path")

    root = _diagnostics_dir(create=False)
    try:
        root_resolved = root.resolve(strict=False)
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ValueError("invalid diagnostics bundle path")

    if candidate_resolved.parent != root_resolved:
        raise ValueError("diagnostics bundle path is outside the profile cache")

    try:
        st = candidate.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(f"cannot inspect diagnostics bundle: {exc}")

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("diagnostics bundle is not a regular file")

    candidate.unlink()
    return True
