"""Local diagnostics bundle RPCs — no support-service upload in the Stardust default path."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tui_gateway import server


def _handler(name: str):
    fn = server._methods.get(name)
    assert fn is not None, f"{name} not registered"
    return fn


@pytest.fixture()
def local_bundle_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import debug

    secret = "sk-" + ("a" * 48)
    monkeypatch.setattr(
        debug,
        "collect_share_bundle",
        lambda **_kwargs: {
            "report": f"report {secret}",
            "gateway.log": f"gateway {secret}",
        },
    )
    monkeypatch.setattr(
        debug,
        "_redact_log_text",
        lambda text: str(text).replace(secret, "[REDACTED]"),
    )
    return home, secret


def test_prepare_bundle_returns_backend_file_without_uploading(local_bundle_home, monkeypatch):
    home, secret = local_bundle_home

    import hermes_cli.diagnostics_upload as upload

    monkeypatch.setattr(
        upload,
        "share_to_nous",
        lambda _blob: (_ for _ in ()).throw(AssertionError("local export attempted Nous upload")),
    )

    result = _handler("diagnostics.prepare_bundle")(
        "rid-prepare",
        {
            "error_context": f"stream failed {secret}",
            "extra_files": {"desktop.log": f"desktop {secret}"},
        },
    )["result"]

    assert result["ok"] is True
    assert result["filename"].startswith("stardust-diagnostics-")
    assert result["filename"].endswith(".zip")
    path = Path(result["path"])
    assert path.parent == home / "cache" / "diagnostics"
    assert result["byte_size"] == path.stat().st_size

    with zipfile.ZipFile(path) as archive:
        assert "manifest.json" in archive.namelist()
        assert "report.txt" in archive.namelist()
        assert "gateway.log" in archive.namelist()
        assert "error-context.txt" in archive.namelist()
        assert "client/desktop.log" in archive.namelist()
        payload = b"\n".join(
            archive.read(name) for name in archive.namelist()
        ).decode("utf-8")
        assert secret not in payload


def test_discard_bundle_is_idempotent_and_scoped(local_bundle_home):
    home, _secret = local_bundle_home
    prepared = _handler("diagnostics.prepare_bundle")("rid-prepare", {})["result"]
    path = Path(prepared["path"])

    first = _handler("diagnostics.discard_bundle")(
        "rid-discard-1", {"path": str(path)}
    )["result"]
    second = _handler("diagnostics.discard_bundle")(
        "rid-discard-2", {"path": str(path)}
    )["result"]

    assert first == {"ok": True, "removed": True}
    assert second == {"ok": True, "removed": False}
    assert path.exists() is False

    outside = home.parent / "stardust-diagnostics-outside.zip"
    outside.write_bytes(b"keep")
    denied = _handler("diagnostics.discard_bundle")(
        "rid-discard-3", {"path": str(outside)}
    )["result"]
    assert denied["ok"] is False
    assert denied["removed"] is False
    assert outside.read_bytes() == b"keep"
