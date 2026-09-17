import json
from pathlib import Path

import pytest

from hermes_cli import assistant_permissions as permissions
from tools import contacts_tool
from tools.registry import registry
from toolsets import resolve_toolset


@pytest.fixture
def contact_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def test_contacts_tool_remember_lookup_list_archive(contact_home):
    remembered = json.loads(registry.dispatch("contacts", {
        "action": "remember",
        "display_name": "王强",
        "aliases": ["老王"],
        "relationship": "同事",
        "channels": [{"channel": "wechat", "handle": "wxid_wang"}],
    }))
    assert remembered["ok"] is True
    contact_id = remembered["contact"]["id"]

    looked_up = json.loads(registry.dispatch("contacts", {
        "action": "lookup", "query": "老王", "channel": "wechat",
    }))
    assert looked_up["found"] is True
    assert looked_up["contact"]["id"] == contact_id
    assert looked_up["contact"]["channels"][0]["handle"] == "wxid_wang"

    listed = json.loads(registry.dispatch("contacts", {"action": "list", "query": "王"}))
    assert listed["count"] == 1
    assert listed["contacts"][0]["display_name"] == "王强"

    archived = json.loads(registry.dispatch("contacts", {
        "action": "archive", "contact_id": contact_id,
    }))
    assert archived == {
        "ok": True, "kind": "contact", "contact_id": contact_id, "archived": True,
    }
    missing = json.loads(registry.dispatch("contacts", {"action": "lookup", "query": "老王"}))
    assert missing["found"] is False


def test_contacts_tool_rejects_duplicate_channel_entries(contact_home):
    result = json.loads(registry.dispatch("contacts", {
        "action": "remember",
        "display_name": "王强",
        "channels": [
            {"channel": "wechat", "handle": "wx1"},
            {"channel": "wechat", "handle": "wx2"},
        ],
    }))
    assert "more than once" in result["error"]


def test_contacts_toolset_is_memory_and_schema_forbids_silent_imports():
    assert "contacts" in resolve_toolset("memory")
    description = contacts_tool.CONTACTS_SCHEMA["description"]
    assert "explicitly supplied or confirmed" in description
    assert "never infer private contact details" in description
    assert "silently import" in description
    assert "Projects" in description


def test_contacts_permission_posture():
    for action in ("lookup", "list"):
        assert permissions.classify_tool_permission("contacts", {"action": action}).level == permissions.ALLOW
    for action in ("remember", "archive"):
        assert permissions.classify_tool_permission("contacts", {"action": action}).level == permissions.NOTIFY
