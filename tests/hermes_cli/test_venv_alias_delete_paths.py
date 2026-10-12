"""Deletes that can reach the venv alias handle it explicitly on Windows (a directory junction).

The Stardust venv alias ``<install>\\venv`` is a directory junction to ``<install>\\.venv``. Three things can reach it:

* ``Path.resolve()`` follows the junction. The dashboard file browser resolved a selected ``venv`` to ``.venv``,
  so deleting the alias deleted the real venv. The route now keeps a selected link as itself and unlinks it.
* A recursive delete of an install root (the uninstaller's checkout and home removal, a failed worktree) first
  unlinks the alias with ``os.rmdir``, then removes the real ``.venv`` as an ordinary folder.
* A real ``venv`` folder is never unlinked: only link entries are removed.

These tests build the layout for real (``mklink /J``, no elevation). Each changed delete path runs against it, and
the assertions check that the ``.venv`` files survive when only the alias should go, and that a link target outside
the deleted tree survives.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server, worktree_ops
from hermes_cli.uninstall import _rmtree_step
from hermes_cli.venv_layout import is_link_entry, unlink_link_entry, unlink_venv_aliases

pytestmark = pytest.mark.windows_only


def _make_junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def _release_junction(link: Path) -> None:
    """Test cleanup: remove the link only. ``os.rmdir`` never follows a junction."""
    if os.path.lexists(link):
        os.rmdir(link)


def _build_checkout(root: Path) -> Path:
    """A checkout with a real ``.venv`` (files included) and the ``venv`` alias inside it."""
    venv = root / ".venv"
    (venv / "Lib").mkdir(parents=True)
    (venv / "Lib" / "keep.txt").write_text("venv file", encoding="utf-8")
    (venv / "Scripts").mkdir()
    (venv / "Scripts" / "python.exe").write_bytes(b"interpreter")
    root.mkdir(parents=True, exist_ok=True)
    _make_junction(root / "venv", venv)
    return venv


def _build_outside_venv(base: Path) -> Path:
    """A folder outside the tree being deleted, reached by a junction inside it."""
    outside = base / "elsewhere" / "venv-target"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("outside", encoding="utf-8")
    return outside


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "hermes-agent"
    venv = _build_checkout(root)
    try:
        yield root, venv
    finally:
        _release_junction(root / "venv")


def test_junction_is_a_link_entry_and_a_real_folder_is_not(checkout):
    root, _venv = checkout

    assert is_link_entry(root / "venv") is True
    assert is_link_entry(root / ".venv") is False
    assert is_link_entry(root / "missing") is False


def test_unlinking_the_alias_keeps_the_venv_files(checkout):
    root, venv = checkout

    unlink_link_entry(root / "venv")

    assert not os.path.lexists(root / "venv")
    assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"


def test_unlink_venv_aliases_removes_only_the_link_and_reports_it(checkout):
    root, venv = checkout

    removed = unlink_venv_aliases(root)

    assert removed == [root / "venv"]
    assert not os.path.lexists(root / "venv")
    assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"


def test_unlink_venv_aliases_never_touches_a_real_venv_folder(tmp_path):
    root = tmp_path / "hermes-agent"
    (root / "venv" / "Lib").mkdir(parents=True)
    (root / "venv" / "Lib" / "keep.txt").write_text("real venv", encoding="utf-8")

    assert unlink_venv_aliases(root) == []
    assert (root / "venv" / "Lib" / "keep.txt").read_text(encoding="utf-8") == "real venv"


def test_checkout_removal_after_the_alias_is_unlinked_removes_the_real_venv(checkout):
    root, venv = checkout

    unlink_venv_aliases(root)
    shutil.rmtree(root)

    assert not root.exists()
    assert not venv.exists()


def test_checkout_removal_never_reaches_a_link_target_outside_it(tmp_path):
    root = tmp_path / "hermes-agent"
    root.mkdir()
    outside = _build_outside_venv(tmp_path)
    _make_junction(root / "venv", outside)
    try:
        unlink_venv_aliases(root)
        shutil.rmtree(root)

        assert not root.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(root / "venv")


def test_uninstall_checkout_removal_unlinks_the_alias_before_the_delete(tmp_path):
    root = tmp_path / "hermes-agent"
    root.mkdir()
    outside = _build_outside_venv(tmp_path)
    _make_junction(root / "venv", outside)
    (root / ".venv").mkdir()
    (root / ".venv" / "keep.txt").write_text("real venv", encoding="utf-8")
    try:
        _rmtree_step(root)

        assert not root.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(root / "venv")


def test_uninstall_home_removal_unlinks_the_managed_checkout_alias(tmp_path):
    home = tmp_path / "home"
    checkout_venv = _build_checkout(home / "hermes-agent")
    outside = _build_outside_venv(tmp_path)
    _make_junction(home / "outside-link", outside)
    try:
        _rmtree_step(home)

        assert not home.exists()
        assert not checkout_venv.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(home / "hermes-agent" / "venv")
        _release_junction(home / "outside-link")


@pytest.fixture
def dashboard_client(monkeypatch, tmp_path):
    managed = tmp_path / "data"
    monkeypatch.setenv("STARDUST_DASHBOARD_FILES_ROOT", str(managed))
    prev_auth = getattr(web_server.app.state, "auth_required", None)
    prev_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield client, managed
    finally:
        client.close()
        if prev_auth is None:
            delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = prev_auth
        if prev_host is None:
            if hasattr(web_server.app.state, "bound_host"):
                delattr(web_server.app.state, "bound_host")
        else:
            web_server.app.state.bound_host = prev_host


def test_dashboard_delete_of_the_alias_keeps_the_venv(dashboard_client):
    client, managed = dashboard_client
    venv = _build_checkout(managed / "hermes-agent")
    try:
        response = client.request(
            "DELETE", "/api/files", json={"path": str(managed / "hermes-agent" / "venv"), "recursive": True}
        )

        assert response.status_code == 200, response.text
        assert not os.path.lexists(managed / "hermes-agent" / "venv")
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(managed / "hermes-agent" / "venv")


def test_dashboard_recursive_delete_of_the_checkout_never_reaches_outside(dashboard_client, tmp_path):
    client, managed = dashboard_client
    _build_checkout(managed / "hermes-agent")
    outside = _build_outside_venv(tmp_path)
    _make_junction(managed / "hermes-agent" / "outside-link", outside)
    try:
        response = client.request(
            "DELETE", "/api/files", json={"path": str(managed / "hermes-agent"), "recursive": True}
        )

        assert response.status_code == 200, response.text
        assert not (managed / "hermes-agent").exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(managed / "hermes-agent" / "outside-link")
        _release_junction(managed / "hermes-agent" / "venv")


def test_failed_worktree_fallback_unlinks_the_alias_and_keeps_the_target(tmp_path, monkeypatch):
    wt_path = tmp_path / "worktree"
    venv = _build_checkout(wt_path)
    outside = _build_outside_venv(tmp_path)
    _make_junction(wt_path / "outside-link", outside)
    # Git refuses to remove the tree here, so the fallback is the path under test.
    monkeypatch.setattr(worktree_ops, "_git_quiet", lambda *args, **kwargs: None)
    try:
        worktree_ops._cleanup_failed_worktree_add(str(tmp_path), wt_path, "feature")

        assert not wt_path.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
        assert not venv.exists()
    finally:
        _release_junction(wt_path / "venv")
        _release_junction(wt_path / "outside-link")
