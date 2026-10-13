import pytest

from agent.lsp.client import LSPClient, file_uri


@pytest.mark.asyncio
async def test_initialize_advertises_navigation_link_support(tmp_path, monkeypatch):
    client = LSPClient(server_id="pyright", workspace_root=str(tmp_path), command=["unused"])
    seen = {}

    async def send_request(method, params):
        seen["request"] = (method, params)
        return {"capabilities": {}}

    async def send_notification(method, params):
        seen.setdefault("notifications", []).append((method, params))

    monkeypatch.setattr(client, "_send_request", send_request)
    monkeypatch.setattr(client, "_send_notification", send_notification)

    await client._initialize()

    method, params = seen["request"]
    assert method == "initialize"
    text_document = params["capabilities"]["textDocument"]
    assert text_document["implementation"]["linkSupport"] is True
    assert text_document["typeDefinition"]["linkSupport"] is True


@pytest.mark.parametrize(
    ("action", "method"),
    [
        ("implementation", "textDocument/implementation"),
        ("type_definition", "textDocument/typeDefinition"),
    ],
)
@pytest.mark.asyncio
async def test_navigation_query_sends_lsp_position(tmp_path, monkeypatch, action, method):
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    client = LSPClient(server_id="pyright", workspace_root=str(tmp_path), command=["unused"])
    seen = {}

    async def sync_file(path, *, language_id="plaintext"):
        seen["sync"] = (path, language_id)

    async def send_request(request_method, params, *, timeout):
        seen["request"] = (request_method, params, timeout)
        return []

    monkeypatch.setattr(client, "sync_file_for_query", sync_file)
    monkeypatch.setattr(client, "_send_request_with_retry", send_request)

    await client.semantic_query(
        str(source), action, line=2, character=7, language_id="python",
    )

    assert seen["sync"] == (str(source), "python")
    request_method, params, timeout = seen["request"]
    assert request_method == method
    assert params == {
        "textDocument": {"uri": file_uri(str(source))},
        "position": {"line": 2, "character": 7},
    }
    assert timeout > 0


@pytest.mark.asyncio
async def test_rename_query_sends_lsp_position_and_new_name(tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("old_name = 1\n")
    client = LSPClient(server_id="pyright", workspace_root=str(tmp_path), command=["unused"])
    seen = {}

    async def sync_file(path, *, language_id="plaintext"):
        seen["sync"] = (path, language_id)

    async def send_request(method, params, *, timeout):
        seen["request"] = (method, params, timeout)
        return {"changes": {}}

    monkeypatch.setattr(client, "sync_file_for_query", sync_file)
    monkeypatch.setattr(client, "_send_request_with_retry", send_request)

    await client.semantic_query(
        str(source), "rename", line=0, character=4, new_name="new_name", language_id="python",
    )

    assert seen["sync"] == (str(source), "python")
    method, params, timeout = seen["request"]
    assert method == "textDocument/rename"
    assert params == {
        "textDocument": {"uri": file_uri(str(source))},
        "position": {"line": 0, "character": 4},
        "newName": "new_name",
    }
    assert timeout > 0
