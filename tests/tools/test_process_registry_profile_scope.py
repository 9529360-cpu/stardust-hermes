"""Process ownership must follow the spawning profile, not shared raw identifiers."""

import json
from pathlib import Path
from unittest.mock import patch

from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
from tools.process_registry import ProcessRegistry


def _scope(home: Path):
    """Bind a test profile; caller resets the returned token."""
    home.mkdir(parents=True, exist_ok=True)
    return set_hermes_home_override(home)


def test_live_lookup_list_and_task_queries_do_not_cross_profiles(tmp_path):
    registry = ProcessRegistry()
    a, b = tmp_path / "a", tmp_path / "b"
    token = _scope(a)
    try:
        own = registry._new_session("private-a", "same-task", "same-task", "same-key", None)
        registry._running[own.id] = own
    finally:
        reset_hermes_home_override(token)
    token = _scope(b)
    try:
        foreign = registry._new_session("private-b", "same-task", "same-task", "same-key", None)
        registry._running[foreign.id] = foreign
        assert registry.get(own.id) is None
        assert registry.get(own.id[5:9]) is None
        assert {p["session_id"] for p in registry.list_sessions()} == {foreign.id}
        assert {p["session_id"] for p in registry.list_sessions("same-task", "same-key")} == {foreign.id}
        assert {s.id for s in registry.running_owned_by("same-task")} == {foreign.id}
        with patch.object(registry, "kill_process", return_value={"status": "killed"}) as kill:
            assert registry.kill_all("same-task") == 1
            kill.assert_called_once()
            assert kill.call_args.args[0] == foreign.id
    finally:
        reset_hermes_home_override(token)


def test_checkpoint_and_receipt_stay_with_owner_when_other_profile_finishes(tmp_path):
    registry = ProcessRegistry()
    a, b = tmp_path / "a", tmp_path / "b"
    token = _scope(a)
    try:
        own = registry._new_session("secret-a", "task", "task", "key", None)
        own.pid = 12345
        registry._running[own.id] = own
        registry._write_checkpoint()
    finally:
        reset_hermes_home_override(token)
    token = _scope(b)
    try:
        foreign = registry._new_session("secret-b", "task", "task", "key", None)
        foreign.pid = 23456
        registry._running[foreign.id] = foreign
        registry._write_checkpoint()
        assert own.id not in (b / "processes.json").read_text(encoding="utf-8")
        registry._move_to_finished(own)
        assert not (b / "logs" / "process-results" / f"{own.id}.json").exists()
    finally:
        reset_hermes_home_override(token)
    assert (a / "logs" / "process-results" / f"{own.id}.json").exists()
    assert foreign.id not in (a / "processes.json").read_text(encoding="utf-8")


def test_legacy_mixed_profile_checkpoints_recover_only_named_namespace(tmp_path, monkeypatch):
    """Pre-upgrade gateways copied both owners into both checkpoint files."""
    registry = ProcessRegistry()
    root = tmp_path / "root"
    default, other = root, root / "profiles" / "other"
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    other.mkdir(parents=True)
    entries = [
        {"session_id": "proc_default", "pid": 111, "command": "default-secret",
         "session_key": "agent:main:telegram:dm:123", "watcher_interval": 5},
        {"session_id": "proc_other", "pid": 222, "command": "other-secret",
         "session_key": "agent:other:telegram:dm:123", "watcher_interval": 5},
        {"session_id": "proc_ambiguous", "pid": 333, "command": "api-secret",
         "session_key": "api-session"},
    ]
    for home in (default, other):
        (home / "processes.json").write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(registry, "_host_pid_is_ours", lambda *_: True)
    token = _scope(default)
    try:
        assert registry.recover_from_checkpoint() == 1
        assert registry.get("proc_default") is not None
        assert registry.get("proc_other") is None
        assert registry.get("proc_ambiguous") is None
        assert [e["session_id"] for e in json.loads((default / "processes.json").read_text())] == ["proc_default"]
    finally:
        reset_hermes_home_override(token)
    token = _scope(other)
    try:
        assert registry.recover_from_checkpoint() == 1
        assert registry.get("proc_other") is not None
        assert registry.get("proc_default") is None
        assert [e["session_id"] for e in json.loads((other / "processes.json").read_text())] == ["proc_other"]
    finally:
        reset_hermes_home_override(token)
    assert {watcher["owner_home"] for watcher in registry.pending_watchers} == {
        hermes_home_key(default), hermes_home_key(other),
    }


def test_unresolved_checkpoint_entry_cannot_migrate_from_foreign_owner(tmp_path):
    registry = ProcessRegistry()
    owner, foreign = tmp_path / "owner", tmp_path / "foreign"
    token = _scope(owner)
    try:
        registry._write_checkpoint(extra_entries=[
            {"session_id": "owned", "owner_home": hermes_home_key(owner)},
            {"session_id": "foreign", "owner_home": hermes_home_key(foreign)},
        ])
        entries = json.loads((owner / "processes.json").read_text(encoding="utf-8"))
        assert [entry["session_id"] for entry in entries] == ["owned"]
    finally:
        reset_hermes_home_override(token)
