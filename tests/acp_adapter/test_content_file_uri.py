"""ACP resource_link file URIs -> readable paths: a Windows drive stays native; only WSL maps it to /mnt/<drive>."""

import base64
from pathlib import PureWindowsPath

import pytest
from acp.schema import ResourceContentBlock

from acp_adapter.content import _content_blocks_to_openai_user_content, _path_from_file_uri

# One Windows file, spelled the ways ACP clients send it.
_DRIVE_URIS = [
    r"C:\Users\me\My Docs\notes.md",
    "C:/Users/me/My Docs/notes.md",
    "file:///C:/Users/me/My%20Docs/notes.md",
    "file://localhost/C:/Users/me/My%20Docs/notes.md",
    "file:C:/Users/me/My%20Docs/notes.md",
    "file:C:\\Users\\me\\My Docs\\notes.md",
]

# 1x1 transparent PNG — smallest valid image payload for inlining tests.
_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


def _slashes(path) -> str:
    return str(path).replace("\\", "/")


@pytest.mark.parametrize("uri", _DRIVE_URIS)
def test_windows_drive_uri_is_not_rewritten_outside_wsl(monkeypatch, uri):
    monkeypatch.setattr("hermes_constants._wsl_detected", False)

    assert _slashes(_path_from_file_uri(uri)) == "C:/Users/me/My Docs/notes.md"


@pytest.mark.parametrize("uri", _DRIVE_URIS)
def test_windows_drive_uri_maps_to_drvfs_mount_inside_wsl(monkeypatch, uri):
    monkeypatch.setattr("hermes_constants._wsl_detected", True)

    assert _path_from_file_uri(uri).as_posix() == "/mnt/c/Users/me/My Docs/notes.md"


@pytest.mark.parametrize("wsl", [False, True])
def test_posix_file_uri_is_unchanged_with_or_without_wsl(monkeypatch, wsl):
    monkeypatch.setattr("hermes_constants._wsl_detected", wsl)

    assert _path_from_file_uri("file:///home/me/notes%20v2.md").as_posix() == "/home/me/notes v2.md"


@pytest.mark.parametrize("uri", ["https://example.com/a.md", "file://fileserver/share/a.md", "  ", ""])
def test_non_local_uris_are_not_readable_paths(uri):
    assert _path_from_file_uri(uri) is None


def test_resource_link_to_local_file_with_space_and_unicode_name_is_inlined(tmp_path):
    attached = tmp_path / "项目 notes.md"
    attached.write_bytes("# 笔记\n\nbody".encode("utf-8"))  # exact bytes: write_text would store CRLF on Windows

    content = _content_blocks_to_openai_user_content([
        ResourceContentBlock(type="resource_link", name="notes", uri=attached.as_uri(), mimeType="text/markdown"),
    ])

    assert "Could not read" not in content
    assert content.endswith("# 笔记\n\nbody")


@pytest.mark.parametrize("name", ["report#1.md", "report%25.md", "My Docs.md", "项目.md"])
@pytest.mark.parametrize("wsl", [False, True])
def test_bare_windows_paths_preserve_literal_filename(monkeypatch, name, wsl):
    monkeypatch.setattr("hermes_constants._wsl_detected", wsl)
    expected = f"/mnt/c/docs/{name}" if wsl else f"C:/docs/{name}"
    for raw in (f"C:/docs/{name}", str(PureWindowsPath("C:/docs") / name)):
        assert _slashes(_path_from_file_uri(raw)) == expected
    # Only actual URIs decode percent escapes, exactly once.
    assert _slashes(_path_from_file_uri((PureWindowsPath("C:/docs") / name).as_uri())) == expected


@pytest.mark.parametrize("as_uri", [False, True])
def test_resource_link_reads_literal_reserved_filename(tmp_path, as_uri):
    attached = tmp_path / "项目 report#1%25 notes.md"
    attached.write_bytes(b"literal filename body")
    content = _content_blocks_to_openai_user_content([
        ResourceContentBlock(type="resource_link", name=attached.name,
                             uri=attached.as_uri() if as_uri else str(attached), mimeType="text/markdown"),
    ])
    assert content.endswith("literal filename body")


def test_resource_link_to_local_image_is_inlined_as_image_url(tmp_path):
    image = tmp_path / "pixel.png"
    image.write_bytes(_ONE_PX_PNG)

    content = _content_blocks_to_openai_user_content([
        ResourceContentBlock(type="resource_link", name="pixel.png", uri=image.as_uri(), mimeType="image/png"),
    ])

    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(_ONE_PX_PNG).decode('ascii')}"},
    }
