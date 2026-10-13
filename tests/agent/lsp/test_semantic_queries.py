import pytest

from agent.lsp.client import LSPClient, file_uri


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
