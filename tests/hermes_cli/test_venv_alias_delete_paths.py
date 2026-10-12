"""Deletes that reach the venv alias, on Windows where the alias is a directory junction.

The Stardust venv alias ``<install>\\venv`` is a junction to ``<install>\\.venv``. Every test builds that layout for
real with ``mklink /J`` (no elevation) and checks one of three things:

* the Files page lists the alias as itself, and deleting it removes only the link, so the real ``.venv`` survives;
* a dangling alias (its ``.venv`` is gone) is listed and removed, instead of failing the listing or answering 404;
* a recursive delete of an install root (the uninstaller's checkout and home removal, a failed worktree) removes the
  alias and the real ``.venv``, and never reaches a folder outside the tree that a junction points to.
* a mutation that touches a venv or .venv folder under the install root is refused, by name and whether or not links are
  involved. Only the alias itself can be unlinked. Names compare case-insensitively here, so ``Venv`` is refused like ``venv``.

``shutil.rmtree`` removes a junction entry without descending into it, so the recursive deletes need no unlink step
first. The guard tests below fail if that stops being true.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server, web_server_files, worktree_ops
from hermes_cli.uninstall import _rmtree_step
from hermes_cli.venv_layout import is_link_entry, unlink_link_entry

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


def test_a_plain_folder_named_venv_is_not_a_link(tmp_path):
    root = tmp_path / "hermes-agent"
    (root / "venv" / "Lib").mkdir(parents=True)
    (root / "venv" / "Lib" / "keep.txt").write_text("real folder", encoding="utf-8")

    assert is_link_entry(root / "venv") is False
    assert (root / "venv" / "Lib" / "keep.txt").read_text(encoding="utf-8") == "real folder"


def test_unlinking_the_alias_keeps_the_venv_files(checkout):
    root, venv = checkout

    unlink_link_entry(root / "venv")

    assert not os.path.lexists(root / "venv")
    assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"


def test_checkout_removal_removes_the_alias_and_the_real_venv(checkout):
    root, venv = checkout

    shutil.rmtree(root)

    assert not os.path.lexists(root)
    assert not venv.exists()


def test_checkout_removal_never_reaches_a_link_target_outside_it(tmp_path):
    root = tmp_path / "hermes-agent"
    root.mkdir()
    outside = _build_outside_venv(tmp_path)
    _make_junction(root / "venv", outside)
    try:
        shutil.rmtree(root)

        assert not os.path.lexists(root)
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(root / "venv")


def test_uninstall_checkout_removal_never_reaches_outside(tmp_path):
    root = tmp_path / "hermes-agent"
    root.mkdir()
    outside = _build_outside_venv(tmp_path)
    _make_junction(root / "venv", outside)
    (root / ".venv").mkdir()
    (root / ".venv" / "keep.txt").write_text("real venv", encoding="utf-8")
    try:
        _rmtree_step(root)

        assert not os.path.lexists(root)
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(root / "venv")


def test_uninstall_home_removal_removes_the_managed_checkout_alias(tmp_path):
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
    # The dashboard runs from this checkout, so its venv folders are the managed venv area.
    monkeypatch.setattr(web_server_files, "_install_root", lambda: managed / "hermes-agent")
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


def _listed(client, folder: Path, name: str) -> dict:
    """The Files page's listing of *folder*, filtered to the entry called *name*."""
    response = client.get("/api/files", params={"path": str(folder)})
    assert response.status_code == 200, response.text
    return next(entry for entry in response.json()["entries"] if entry["name"] == name)


def test_dashboard_lists_the_alias_as_a_link_under_its_own_path(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    try:
        entry = _listed(client, checkout, "venv")

        assert entry["is_link"] is True
        assert entry["broken"] is False
        assert entry["path"] == str(checkout / "venv")
    finally:
        _release_junction(checkout / "venv")


def test_dashboard_lists_a_link_to_outside_without_following_it(dashboard_client, tmp_path):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    checkout.mkdir(parents=True)
    outside = _build_outside_venv(tmp_path)
    _make_junction(checkout / "outside-link", outside)
    try:
        entry = _listed(client, checkout, "outside-link")

        assert entry["is_link"] is True
        assert entry["path"] == str(checkout / "outside-link")
    finally:
        _release_junction(checkout / "outside-link")


def test_dashboard_listing_then_delete_of_the_alias_keeps_the_venv(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    try:
        # The Files page sends back the listed path, with recursive set for a folder entry.
        entry = _listed(client, checkout, "venv")
        response = client.request(
            "DELETE", "/api/files", json={"path": entry["path"], "recursive": entry["is_directory"]}
        )

        assert response.status_code == 200, response.text
        assert (venv / "Lib" / "keep.txt").is_file(), "the real .venv was deleted through the listed path"
        assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"
        assert not os.path.lexists(checkout / "venv")
    finally:
        _release_junction(checkout / "venv")


def test_dashboard_lists_and_deletes_a_dangling_alias(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    shutil.rmtree(checkout / ".venv")  # the alias now dangles
    try:
        entry = _listed(client, checkout, "venv")

        assert entry["is_link"] is True
        assert entry["broken"] is True
        assert entry["is_directory"] is False

        response = client.request("DELETE", "/api/files", json={"path": entry["path"], "recursive": False})

        assert response.status_code == 200, response.text
        assert not os.path.lexists(checkout / "venv")
    finally:
        _release_junction(checkout / "venv")


def test_recursive_delete_of_a_folder_with_an_outside_link_never_reaches_outside(dashboard_client, tmp_path):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    work = checkout / "work"
    work.mkdir()
    outside = _build_outside_venv(tmp_path)
    _make_junction(work / "outside-link", outside)
    try:
        whole = client.request("DELETE", "/api/files", json={"path": str(checkout), "recursive": True})

        assert whole.status_code == 403, whole.text
        assert checkout.exists()

        response = client.request("DELETE", "/api/files", json={"path": str(work), "recursive": True})

        assert response.status_code == 200, response.text
        assert not os.path.lexists(work)
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(work / "outside-link")
        _release_junction(checkout / "venv")


def test_direct_delete_inside_dot_venv_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    try:
        response = client.request("DELETE", "/api/files", json={"path": str(venv / "Lib"), "recursive": True})

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
        assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"
    finally:
        _release_junction(checkout / "venv")


def test_a_link_to_the_install_root_followed_by_a_delete_below_it_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    work = managed / "work"
    work.mkdir()
    _make_junction(work / "rootlink", checkout)
    try:
        response = client.request(
            "DELETE", "/api/files", json={"path": str(work / "rootlink" / ".venv" / "Lib"), "recursive": True}
        )

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(work / "rootlink")
        _release_junction(checkout / "venv")


def test_a_two_link_chain_into_dot_venv_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    work = managed / "work"
    work.mkdir()
    _make_junction(work / "b", venv)
    _make_junction(work / "a", work / "b")
    try:
        response = client.request("DELETE", "/api/files", json={"path": str(work / "a" / "Lib"), "recursive": True})

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(work / "a")
        _release_junction(work / "b")
        _release_junction(checkout / "venv")


def test_a_link_inside_dot_venv_pointing_back_to_the_root_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    _make_junction(venv / "back", checkout)
    try:
        link = client.request("DELETE", "/api/files", json={"path": str(venv / "back"), "recursive": True})
        below = client.request("DELETE", "/api/files", json={"path": str(venv / "back" / "Lib"), "recursive": True})

        assert link.status_code == 403, link.text
        assert below.status_code == 403, below.text
        assert os.path.lexists(venv / "back")
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(venv / "back")
        _release_junction(checkout / "venv")


def test_the_listing_path_of_dot_venv_is_refused_while_the_alias_unlinks_alone(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    try:
        real = _listed(client, checkout, ".venv")
        alias = _listed(client, checkout, "venv")

        refused = client.request("DELETE", "/api/files", json={"path": real["path"], "recursive": real["is_directory"]})
        child = client.request("DELETE", "/api/files", json={"path": str(checkout / "venv" / "Lib"), "recursive": True})
        unlinked = client.request(
            "DELETE", "/api/files", json={"path": alias["path"], "recursive": alias["is_directory"]}
        )

        assert refused.status_code == 403, refused.text
        assert child.status_code == 403, child.text
        assert unlinked.status_code == 200, unlinked.text
        assert not os.path.lexists(checkout / "venv")
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(checkout / "venv")


def test_a_final_link_into_dot_venv_is_refused_because_only_the_alias_may_be_unlinked(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    work = managed / "work"
    work.mkdir()
    _make_junction(work / "toVenv", venv)
    try:
        response = client.request("DELETE", "/api/files", json={"path": str(work / "toVenv"), "recursive": True})

        assert response.status_code == 403, response.text
        assert os.path.lexists(work / "toVenv")
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(work / "toVenv")
        _release_junction(checkout / "venv")


def test_a_recursive_delete_of_a_folder_holding_a_venv_under_the_root_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    tools = checkout / "tools"
    nested = tools / "helper" / ".venv"
    (nested / "Lib").mkdir(parents=True)
    (nested / "Lib" / "keep.txt").write_text("nested venv", encoding="utf-8")
    try:
        response = client.request("DELETE", "/api/files", json={"path": str(tools), "recursive": True})

        assert response.status_code == 403, response.text
        assert (nested / "Lib" / "keep.txt").read_text(encoding="utf-8") == "nested venv"
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(checkout / "venv")


def test_a_dot_venv_folder_without_an_interpreter_is_protected_by_name(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = checkout / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "keep.txt").write_text("venv file", encoding="utf-8")

    response = client.request("DELETE", "/api/files", json={"path": str(venv / "lib"), "recursive": True})

    assert response.status_code == 403, response.text
    assert (venv / "lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"


def test_dashboard_recursive_delete_through_the_alias_folder_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    try:
        response = client.request(
            "DELETE", "/api/files", json={"path": str(checkout / "venv" / "Lib"), "recursive": True}
        )

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
        assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"
    finally:
        _release_junction(checkout / "venv")


def test_dashboard_delete_of_a_file_through_the_alias_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    try:
        response = client.request(
            "DELETE", "/api/files", json={"path": str(checkout / "venv" / "Lib" / "keep.txt"), "recursive": False}
        )

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(checkout / "venv")


def test_a_link_inside_a_real_folder_is_refused_and_deleting_the_folder_keeps_the_venv(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    work = checkout / "work"
    work.mkdir()
    _make_junction(work / "link", venv)
    try:
        refused = client.request("DELETE", "/api/files", json={"path": str(work / "link" / "Lib"), "recursive": True})

        assert refused.status_code == 403, refused.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"

        removed = client.request("DELETE", "/api/files", json={"path": str(work), "recursive": True})

        assert removed.status_code == 200, removed.text
        assert not os.path.lexists(work)
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
        assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"
    finally:
        _release_junction(work / "link")
        _release_junction(checkout / "venv")


def test_a_link_from_outside_the_root_into_the_venv_is_refused(dashboard_client, tmp_path):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    outside = tmp_path / "elsewhere" / "alias"
    outside.parent.mkdir()
    _make_junction(outside, venv)
    try:
        response = client.request("DELETE", "/api/files", json={"path": str(outside / "Lib"), "recursive": True})

        assert response.status_code == 403, response.text
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(outside)
        _release_junction(checkout / "venv")


def test_mutating_calls_through_the_alias_are_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    alias = checkout / "venv"
    data_url = "data:text/plain;base64,eA=="
    try:
        mkdir = client.post("/api/files/mkdir", json={"path": str(alias / "Lib" / "newdir")})
        upload = client.post(
            "/api/files/upload",
            json={"path": str(alias / "Lib" / "new.txt"), "data_url": data_url, "overwrite": True},
        )
        write = client.post(
            "/api/fs/write-text", json={"path": str(alias / "Lib" / "keep.txt"), "content": "changed"}
        )
        onto_alias = client.post("/api/files/upload", json={"path": str(alias), "data_url": data_url, "overwrite": True})

        assert mkdir.status_code == 403, mkdir.text
        assert upload.status_code == 403, upload.text
        assert write.status_code == 403, write.text
        assert onto_alias.status_code == 403, onto_alias.text
        assert not os.path.lexists(venv / "Lib" / "newdir")
        assert not os.path.lexists(venv / "Lib" / "new.txt")
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
        assert (venv / "Scripts" / "python.exe").read_bytes() == b"interpreter"
    finally:
        _release_junction(alias)


def test_an_ordinary_link_to_a_folder_outside_the_venv_area_still_writes_and_deletes(dashboard_client):
    client, managed = dashboard_client
    _build_checkout(managed / "hermes-agent")
    project = managed / "elsewhere" / "project"
    project.mkdir(parents=True)
    (project / "keep.txt").write_text("project file", encoding="utf-8")
    work = managed / "work"
    work.mkdir()
    _make_junction(work / "ordinary", project)
    try:
        written = client.post(
            "/api/files/upload",
            json={"path": str(work / "ordinary" / "new.txt"), "data_url": "data:text/plain;base64,eA=="},
        )
        assert written.status_code == 200, written.text
        assert (project / "new.txt").read_text(encoding="utf-8") == "x"

        edited = client.post(
            "/api/fs/write-text", json={"path": str(work / "ordinary" / "keep.txt"), "content": "changed"}
        )
        assert edited.status_code == 200, edited.text
        assert (project / "keep.txt").read_text(encoding="utf-8") == "changed"

        deleted = client.request(
            "DELETE", "/api/files", json={"path": str(work / "ordinary" / "new.txt"), "recursive": False}
        )
        assert deleted.status_code == 200, deleted.text
        assert not (project / "new.txt").exists()

        unlinked = client.request("DELETE", "/api/files", json={"path": str(work / "ordinary"), "recursive": True})
        assert unlinked.status_code == 200, unlinked.text
        assert not os.path.lexists(work / "ordinary")
        assert (project / "keep.txt").read_text(encoding="utf-8") == "changed"
    finally:
        _release_junction(work / "ordinary")


def test_failed_worktree_fallback_removes_the_alias_and_keeps_the_target(tmp_path, monkeypatch):
    wt_path = tmp_path / "worktree"
    venv = _build_checkout(wt_path)
    outside = _build_outside_venv(tmp_path)
    _make_junction(wt_path / "outside-link", outside)
    # Git refuses to remove the tree here, so the fallback is the path under test.
    monkeypatch.setattr(worktree_ops, "_git_quiet", lambda *args, **kwargs: None)
    try:
        worktree_ops._cleanup_failed_worktree_add(str(tmp_path), wt_path, "feature")

        assert not os.path.lexists(wt_path)
        assert not venv.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _release_junction(wt_path / "venv")
        _release_junction(wt_path / "outside-link")


def _mixed_case_venv(checkout: Path) -> Path:
    """A real venv folder spelled ``Venv`` under ``tools2``. On NTFS, ``venv`` and ``VENV`` name the same folder."""
    mixed = checkout / "tools2" / "Venv"
    (mixed / "Lib").mkdir(parents=True)
    (mixed / "Lib" / "keep.txt").write_text("mixed venv", encoding="utf-8")
    return mixed


def test_mixed_case_venv_is_refused_by_upload(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    mixed = _mixed_case_venv(checkout)
    try:
        response = client.post(
            "/api/files/upload",
            json={
                "path": str(mixed / "Lib" / "planted.txt"),
                "data_url": "data:text/plain;base64,eA==",
                "overwrite": True,
            },
        )

        assert response.status_code == 403, response.text
        assert not (mixed / "Lib" / "planted.txt").exists()
        assert (mixed / "Lib" / "keep.txt").read_text(encoding="utf-8") == "mixed venv"
    finally:
        _release_junction(checkout / "venv")


def test_mixed_case_venv_is_refused_by_upload_stream(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    mixed = _mixed_case_venv(checkout)
    try:
        # Upper case names the same folder as Venv on NTFS, and is not the lower-case spelling the guard lists.
        response = client.post(
            "/api/files/upload-stream",
            data={"path": str(checkout / "tools2" / "VENV" / "Lib" / "planted.txt"), "overwrite": "true"},
            files={"file": ("planted.txt", b"planted", "text/plain")},
        )

        assert response.status_code == 403, response.text
        assert not (mixed / "Lib" / "planted.txt").exists()
        assert (mixed / "Lib" / "keep.txt").read_text(encoding="utf-8") == "mixed venv"
    finally:
        _release_junction(checkout / "venv")


def test_mixed_case_venv_is_refused_by_mkdir(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    mixed = _mixed_case_venv(checkout)
    try:
        response = client.post(
            "/api/files/mkdir", json={"path": str(checkout / "tools2" / "VENV" / "Lib" / "newdir")}
        )

        assert response.status_code == 403, response.text
        assert not (mixed / "Lib" / "newdir").exists()
        assert (mixed / "Lib" / "keep.txt").read_text(encoding="utf-8") == "mixed venv"
    finally:
        _release_junction(checkout / "venv")


def test_mixed_case_venv_is_refused_by_write_text(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    _build_checkout(checkout)
    mixed = _mixed_case_venv(checkout)
    try:
        response = client.post(
            "/api/fs/write-text", json={"path": str(mixed / "Lib" / "keep.txt"), "content": "changed"}
        )

        assert response.status_code == 403, response.text
        assert (mixed / "Lib" / "keep.txt").read_text(encoding="utf-8") == "mixed venv"
    finally:
        _release_junction(checkout / "venv")


def test_recursive_delete_of_a_folder_holding_a_mixed_case_venv_is_refused(dashboard_client):
    client, managed = dashboard_client
    checkout = managed / "hermes-agent"
    venv = _build_checkout(checkout)
    mixed = _mixed_case_venv(checkout)
    try:
        whole = client.request("DELETE", "/api/files", json={"path": str(checkout / "tools2"), "recursive": True})
        inside = client.request(
            "DELETE", "/api/files", json={"path": str(checkout / "tools2" / "VENV" / "Lib"), "recursive": True}
        )
        dotted = client.request(
            "DELETE", "/api/files", json={"path": str(checkout / ".VENV" / "Lib"), "recursive": True}
        )

        assert whole.status_code == 403, whole.text
        assert inside.status_code == 403, inside.text
        assert dotted.status_code == 403, dotted.text
        assert (mixed / "Lib" / "keep.txt").read_text(encoding="utf-8") == "mixed venv"
        assert (venv / "Lib" / "keep.txt").read_text(encoding="utf-8") == "venv file"
    finally:
        _release_junction(checkout / "venv")
