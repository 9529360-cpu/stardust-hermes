from __future__ import annotations

import json
import os
import stat
import time
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def diagnostics_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import debug

    monkeypatch.setattr(
        debug,
        "collect_share_bundle",
        lambda **_kwargs: {
            "report": "report SECRET",
            "agent.log": "agent SECRET",
        },
    )
    monkeypatch.setattr(
        debug,
        "_redact_log_text",
        lambda text: str(text).replace("SECRET", "[REDACTED]"),
    )
    return home


def test_prepare_bundle_is_local_redacted_private_and_discardable(diagnostics_home):
    from hermes_cli.diagnostics_export import (
        discard_diagnostics_bundle,
        prepare_diagnostics_bundle,
    )

    result = prepare_diagnostics_bundle(
        error_context="stream failed SECRET",
        extra_files={"../desktop.log": "desktop SECRET"},
        log_lines=200,
    )

    path = Path(str(result["path"]))
    assert path.parent == diagnostics_home / "cache" / "diagnostics"
    assert path.name == result["filename"]
    assert path.name.startswith("stardust-diagnostics-")
    assert result["byte_size"] == path.stat().st_size
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "report.txt",
            "agent.log",
            "error-context.txt",
            "client/desktop.log",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest == {
            "format": "stardust-diagnostics-v1",
            "created": manifest["created"],
            "redacted": True,
            "temporary_backend_copy": True,
        }
        payload = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name != "manifest.json"
        )
        assert "SECRET" not in payload
        assert "[REDACTED]" in payload

    assert discard_diagnostics_bundle(str(path)) is True
    assert path.exists() is False
    assert discard_diagnostics_bundle(str(path)) is False


def test_discard_refuses_paths_outside_diagnostics_cache(diagnostics_home, tmp_path):
    from hermes_cli.diagnostics_export import discard_diagnostics_bundle

    outside = tmp_path / "stardust-diagnostics-outside.zip"
    outside.write_bytes(b"do not delete")

    with pytest.raises(ValueError, match="outside the profile cache"):
        discard_diagnostics_bundle(str(outside))

    assert outside.read_bytes() == b"do not delete"


def test_prune_never_deletes_the_current_handoff_when_mtimes_tie(diagnostics_home, monkeypatch):
    import hermes_cli.diagnostics_export as export

    root = diagnostics_home / "cache" / "diagnostics"
    root.mkdir(parents=True)
    monkeypatch.setattr(export, "_MAX_EXPORTS", 2)

    paths = [
        root / f"stardust-diagnostics-tied-{index}.zip"
        for index in range(3)
    ]
    for path in paths:
        path.write_bytes(b"zip")
        path.touch()

    same_time = time.time()
    for path in paths:
        os.utime(path, (same_time, same_time))

    export._prune_exports(root, keep=paths[-1])

    remaining = set(root.glob("stardust-diagnostics-*.zip"))
    assert paths[-1] in remaining
    assert len(remaining) == 2


def test_prepare_bounds_client_files_and_prunes_crash_leftovers(diagnostics_home, monkeypatch):
    import hermes_cli.diagnostics_export as export

    root = diagnostics_home / "cache" / "diagnostics"
    root.mkdir(parents=True)
    monkeypatch.setattr(export, "_MAX_EXPORTS", 2)

    first = Path(str(export.prepare_diagnostics_bundle()["path"]))
    os.utime(first, (1, 1))
    second = Path(str(export.prepare_diagnostics_bundle()["path"]))
    os.utime(second, (2, 2))
    third = Path(str(export.prepare_diagnostics_bundle()["path"]))

    existing = sorted(root.glob("stardust-diagnostics-*.zip"))
    assert len(existing) == 2
    assert third in existing
    assert second in existing
    assert first not in existing

    with zipfile.ZipFile(third) as archive:
        assert "manifest.json" in archive.namelist()
