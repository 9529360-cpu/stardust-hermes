"""File URI round trips through the real default image dispatch boundary."""
import asyncio
from pathlib import Path, PureWindowsPath

import pytest

from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms._shared import file_uri_to_path


@pytest.mark.windows_only
@pytest.mark.parametrize("uri", [
    "file:///C:/docs/report%2325%2525%20项目.png",
    "file://localhost/C:/docs/report%2325%2525%20项目.png",
    "file://C:/docs/report%2325%2525%20项目.png",
])
def test_local_drive_file_uri_compatibility(uri):
    assert file_uri_to_path(uri) == str(PureWindowsPath("C:/docs/report#25%25 项目.png"))


class FileAdapter(BasePlatformAdapter):
    name = "file-test"

    def __init__(self):
        self.paths = []
        self.payloads = []

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, **kwargs):
        return SendResult(success=True)

    async def get_chat_info(self, chat_id):
        return {}

    async def send_image_file(self, chat_id, image_path, caption=None, **kwargs):
        self.paths.append(image_path)
        return SendResult(success=True)


@pytest.mark.windows_only
@pytest.mark.parametrize("root", ["C:/docs", r"\\server\share\docs"])
@pytest.mark.parametrize("name", ["report#1.png", "report%25.png", "my image.png", "项目.png"])
def test_windows_image_batch_roundtrip(root, name):
    path = PureWindowsPath(root) / name
    adapter = FileAdapter()
    result = asyncio.run(adapter.send_multiple_images("local-test", [(path.as_uri(), "caption")]))
    assert result.success
    assert adapter.paths == [str(path)]


@pytest.mark.parametrize("name", ["report#1.png", "report%25.png", "my image.png", "项目.png"])
def test_image_batch_opens_original_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"original image bytes")
    adapter = FileAdapter()

    async def read_image(chat_id, image_path, **kwargs):
        adapter.payloads.append(Path(image_path).read_bytes())
        return SendResult(success=True)

    adapter.send_image_file = read_image
    result = asyncio.run(adapter.send_multiple_images("local-test", [(path.as_uri(), "")]))
    assert result.success
    assert adapter.payloads == [b"original image bytes"]
