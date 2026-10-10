"""tool_search marks the MCP tools that will ask the user before they run.

``approval_required`` is a routing hint built from the same MCP state the call-time approval check
reads: a tool on an untrusted server that is not annotated read-only. It informs the model's plan.
It never gates the call; the call-time check still decides.
"""

import json

import pytest

from tools import mcp_tool, tool_search
from tools.tool_search import _mcp_permission_snapshot, _shared_tool_record
from tools.tool_search_catalog import CatalogEntry, _entry_search_text, _tokenize


def _mcp_entry(name: str, description: str, server: str = "mail") -> CatalogEntry:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    entry = CatalogEntry(
        name=name,
        description=description,
        schema=schema,
        source="mcp",
        source_name=server,
    )
    entry._tokens = _tokenize(_entry_search_text(schema))
    return entry


@pytest.fixture
def mcp_state(monkeypatch):
    """Isolate the process-global MCP state the snapshot reads, with identity server keys."""
    from tools import mcp_tool_scope

    monkeypatch.setattr(mcp_tool, "_mcp_tool_server_names", {})
    monkeypatch.setattr(mcp_tool, "_tool_read_only_hints", {})
    monkeypatch.setattr(mcp_tool, "_server_trust_levels", {})
    monkeypatch.setattr(mcp_tool_scope, "_resolve_server_key", lambda server: server)
    monkeypatch.setattr(mcp_tool_scope, "_server_key", lambda server: server)
    return mcp_tool


def test_untrusted_server_tool_that_is_not_read_only_asks_first(mcp_state):
    entry = _mcp_entry("mail__send_message", "send a mail message")
    mcp_state._mcp_tool_server_names["mail__send_message"] = "mail"
    mcp_state._server_trust_levels["mail"] = mcp_state._TRUST_UNTRUSTED

    record = _shared_tool_record(
        entry, None, _mcp_permission_snapshot([entry])["mail__send_message"]
    )

    assert record["approval_required"] is True


def test_read_only_tool_on_an_untrusted_server_does_not_ask(mcp_state):
    entry = _mcp_entry("mail__list_inbox", "list recent messages")
    mcp_state._mcp_tool_server_names["mail__list_inbox"] = "mail"
    mcp_state._server_trust_levels["mail"] = mcp_state._TRUST_UNTRUSTED
    mcp_state._tool_read_only_hints["mail"] = {"list_inbox": True}

    record = _shared_tool_record(
        entry, None, _mcp_permission_snapshot([entry])["mail__list_inbox"]
    )

    assert "approval_required" not in record


def test_tool_on_a_fully_trusted_server_does_not_ask(mcp_state):
    entry = _mcp_entry("mail__send_message", "send a mail message")
    mcp_state._mcp_tool_server_names["mail__send_message"] = "mail"
    mcp_state._server_trust_levels["mail"] = mcp_state._TRUST_FULL

    record = _shared_tool_record(
        entry, None, _mcp_permission_snapshot([entry])["mail__send_message"]
    )

    assert "approval_required" not in record


def test_builtin_tools_are_outside_the_snapshot_and_carry_no_hint():
    builtin = CatalogEntry(
        name="web_search",
        description="search the web",
        schema={
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        source="builtin",
        source_name="web",
    )

    assert _mcp_permission_snapshot([builtin]) == {}
    assert "approval_required" not in _shared_tool_record(builtin, None, None)


def test_dispatch_flags_the_untrusted_send_tool_and_not_the_read_only_one(
    mcp_state, monkeypatch
):
    send = _mcp_entry("mail__send_message", "send a mail message to a recipient")
    listing = _mcp_entry("mail__list_inbox", "list recent mail messages in the inbox")
    mcp_state._mcp_tool_server_names.update({
        "mail__send_message": "mail",
        "mail__list_inbox": "mail",
    })
    mcp_state._server_trust_levels["mail"] = mcp_state._TRUST_UNTRUSTED
    mcp_state._tool_read_only_hints["mail"] = {"list_inbox": True}
    monkeypatch.setattr(tool_search, "build_catalog", lambda _defs: [send, listing])

    out = json.loads(
        tool_search.dispatch_tool_search(
            {"queries": ["send mail message"]}, current_tool_defs=[]
        )
    )

    assert out["tools"]["mail__send_message"]["approval_required"] is True
    assert "approval_required" not in out["tools"].get("mail__list_inbox", {})
