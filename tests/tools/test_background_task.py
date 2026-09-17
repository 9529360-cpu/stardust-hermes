import json
from pathlib import Path

import pytest

from hermes_cli import assistant_permissions as permissions
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools import background_task as bt
from tools.registry import registry
from toolsets import resolve_toolset


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    identities = getattr(kb, "_INITIALIZED_FILE_IDENTITIES", None)
    if identities is not None:
        identities.pop(str(db_path.resolve()), None)
    kb.init_db()
    return home


def test_start_defaults_assignee_and_surfaces_delivery_truth(monkeypatch):
    seen = {}
    monkeypatch.setattr(bt, "_default_assignee", lambda: "desktop-main")

    def dispatch(name, payload, kwargs):
        seen.update(name=name, payload=dict(payload), kwargs=dict(kwargs))
        return json.dumps({
            "ok": True,
            "task_id": "bg-1",
            "status": "running",
            "workspace_path": "C:/internal/worktree",
            "subscribed": True,
        })

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)
    result = json.loads(registry.dispatch(
        "background_task",
        {"action": "start", "title": "Prepare report", "body": "Create the report"},
        session_id="session-1",
    ))

    assert seen["name"] == "kanban_create"
    assert seen["payload"]["assignee"] == "desktop-main"
    assert seen["payload"]["title"] == "Prepare report"
    assert seen["kwargs"]["session_id"] == "session-1"
    assert result["task_id"] == "bg-1"
    assert result["state"] == "running"
    assert result["terminal"] is False
    assert result["subscribed"] is True
    assert result["notification_mode"] == "automatic"
    assert result["kind"] == "background_task"
    assert "status" not in result
    assert "workspace_path" not in result


def test_start_preserves_explicit_specialist_and_manual_delivery(monkeypatch):
    seen = {}
    monkeypatch.setattr(bt, "_default_assignee", lambda: "should-not-be-used")

    def dispatch(name, payload, kwargs):
        seen.update(name=name, payload=dict(payload))
        return json.dumps({"ok": True, "task_id": "bg-2", "status": "ready", "subscribed": False})

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)
    result = json.loads(registry.dispatch(
        "background_task",
        {"action": "start", "title": "Review code", "assignee": "reviewer"},
    ))

    assert seen["name"] == "kanban_create"
    assert seen["payload"]["assignee"] == "reviewer"
    assert result["state"] == "queued"
    assert result["notification_mode"] == "manual"


def test_read_and_mutation_actions_route_to_existing_kernel_handlers(monkeypatch):
    calls = []

    def dispatch(name, payload, kwargs):
        calls.append((name, dict(payload)))
        if name == "kanban_show":
            return json.dumps({"task": {"id": "t1", "status": "running"}})
        if name == "kanban_list":
            return json.dumps({"tasks": [], "count": 0, "limit": 5, "truncated": False})
        return json.dumps({"ok": True, "task_id": "t1", "status": "ready"})

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)
    monkeypatch.setattr(bt, "_pending_approvals", lambda _task_id: [])

    registry.dispatch("background_task", {"action": "status", "task_id": "t1"})
    registry.dispatch("background_task", {"action": "list", "state": "running", "limit": 5})
    registry.dispatch("background_task", {"action": "comment", "task_id": "t1", "body": "note"})
    registry.dispatch("background_task", {"action": "resume", "task_id": "t1"})

    assert calls == [
        ("kanban_show", {"task_id": "t1"}),
        ("kanban_list", {"limit": 5, "status": "running"}),
        ("kanban_comment", {"task_id": "t1", "body": "note"}),
        ("kanban_unblock", {"task_id": "t1"}),
        ("kanban_show", {"task_id": "t1"}),
    ]


def test_status_projects_kernel_state_without_worker_internals(monkeypatch):
    raw = {
        "task": {
            "id": "t1", "title": "Prepare client pack", "status": "blocked",
            "priority": 7, "assignee": "worker-a", "workspace_path": "C:/secret/worktree",
            "current_run_id": 99, "result": None, "created_at": 100, "started_at": 110,
        },
        "parents": ["parent-1"],
        "children": ["child-1"],
        "comments": [{"author": "worker-a", "body": "internal note"}],
        "worker_context": "INTERNAL WORKER PROMPT",
        "events": [
            {"kind": "assistant_approval_requested", "payload": {"approval_id": "apr-1"}},
            {"kind": "blocked", "payload": {"reason": "Waiting for user approval apr-1 before send_message."}},
        ],
        "runs": [{
            "id": 99, "profile": "worker-a", "status": "ended", "outcome": "blocked",
            "summary": "draft ready", "metadata": {"secret": "no"}, "started_at": 110, "ended_at": 120,
        }],
    }
    monkeypatch.setattr(bt, "_dispatch_kanban", lambda *_args, **_kwargs: json.dumps(raw))
    monkeypatch.setattr(bt, "_pending_approvals", lambda _task_id: [{
        "approval_id": "apr-1", "tool_name": "send_message", "reason": "Send client reply",
        "requested_at": 120, "state": "pending",
    }])

    result = json.loads(registry.dispatch(
        "background_task", {"action": "status", "task_id": "t1"}
    ))

    assert result["kind"] == "background_task"
    assert result["task_id"] == "t1"
    assert result["state"] == "waiting_confirmation"
    assert result["waiting"] is True
    assert result["needs_user"] is True
    assert result["terminal"] is False
    assert result["dependencies"] == ["parent-1"]
    assert result["dependents"] == ["child-1"]
    assert result["pending_approvals"][0]["approval_id"] == "apr-1"
    assert result["latest_attempt"]["summary"] == "draft ready"
    assert "status" not in result
    encoded = json.dumps(result)
    for internal in ("worker_context", "workspace_path", "current_run_id", "assignee", "profile", "metadata"):
        assert internal not in encoded


def test_list_projects_compact_background_task_summaries(monkeypatch):
    raw = {
        "tasks": [{
            "id": "t1", "title": "Prepare report", "status": "running", "priority": 3,
            "assignee": "worker-a", "workspace_path": "/private/worktree", "current_run_id": 5,
            "parent_count": 1, "child_count": 2, "project_id": "proj-1", "created_at": 100,
        }],
        "count": 1, "limit": 20, "truncated": False, "next_limit": None, "promoted": ["x"],
    }
    monkeypatch.setattr(bt, "_dispatch_kanban", lambda *_args, **_kwargs: json.dumps(raw))

    result = json.loads(registry.dispatch(
        "background_task", {"action": "list", "limit": 20}
    ))

    assert result["kind"] == "background_task_list"
    assert result["tasks"] == [{
        "task_id": "t1", "title": "Prepare report", "state": "running",
        "terminal": False, "waiting": False, "needs_user": False, "priority": 3,
        "created_at": 100, "dependency_count": 1, "dependent_count": 2, "project_id": "proj-1",
    }]
    encoded = json.dumps(result)
    for internal in ("status", "assignee", "workspace_path", "current_run_id", "promoted"):
        assert internal not in encoded


def test_list_failed_filter_requires_real_failure_evidence(monkeypatch):
    calls = []
    summaries = {
        "tasks": [
            {"id": "failed-1", "title": "Failed task", "status": "blocked", "parent_count": 0, "child_count": 0},
            {"id": "approval-1", "title": "Approval task", "status": "blocked", "parent_count": 0, "child_count": 0},
        ],
        "count": 2,
        "limit": 200,
        "truncated": False,
    }

    def dispatch(name, payload, kwargs):
        calls.append((name, dict(payload)))
        if name == "kanban_list":
            return json.dumps(summaries)
        if payload["task_id"] == "failed-1":
            return json.dumps({
                "task": {"id": "failed-1", "status": "blocked", "last_failure_error": "boom"},
                "events": [
                    {"kind": "blocked", "payload": {"reason": "worker failed"}},
                    {"kind": "gave_up", "payload": {"error": "boom"}},
                ],
                "parents": [],
            })
        return json.dumps({
            "task": {"id": "approval-1", "status": "blocked"},
            "events": [
                {"kind": "blocked", "payload": {"kind": "needs_input", "reason": "approval required"}},
                {"kind": "assistant_approval_requested", "payload": {"approval_id": "apr-1"}},
            ],
            "parents": [],
        })

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)
    monkeypatch.setattr(
        bt,
        "_pending_approvals",
        lambda task_id: ([{"approval_id": "apr-1", "state": "pending"}] if task_id == "approval-1" else []),
    )

    result = json.loads(registry.dispatch(
        "background_task", {"action": "list", "state": "failed", "limit": 10}
    ))

    assert calls[0] == ("kanban_list", {"limit": 200, "status": "blocked"})
    assert [item["task_id"] for item in result["tasks"]] == ["failed-1"]
    assert result["tasks"][0]["state"] == "failed"
    assert result["tasks"][0]["terminal"] is True


def test_unfiltered_blocked_list_summary_fails_visible_instead_of_guessing(monkeypatch):
    raw = {
        "tasks": [{"id": "t1", "title": "Blocked", "status": "blocked", "parent_count": 0, "child_count": 0}],
        "count": 1,
        "limit": 20,
        "truncated": False,
    }
    monkeypatch.setattr(bt, "_dispatch_kanban", lambda *_args, **_kwargs: json.dumps(raw))
    result = json.loads(registry.dispatch("background_task", {"action": "list", "limit": 20}))
    assert result["tasks"][0]["state"] == "needs_attention"
    assert result["tasks"][0]["needs_user"] is True


def test_approval_actions_use_durable_approval_owner(monkeypatch):
    calls = []

    def operation(task_id, action, args):
        calls.append((task_id, action, dict(args)))
        return json.dumps({"ok": True, "task_id": task_id, "action": action})

    monkeypatch.setattr(bt, "_approval_operation", operation)
    for action in ("approvals", "approve", "deny"):
        result = json.loads(registry.dispatch(
            "background_task",
            {"action": action, "task_id": "t1", "approval_id": "apr-1"},
        ))
        assert result["action"] == action
    assert [item[1] for item in calls] == ["approvals", "approve", "deny"]


def test_approval_projection_hides_hash_and_rule_key():
    projected = bt._public_approval({
        "approval_id": "apr-1", "tool_name": "send_message", "reason": "Send reply",
        "requested_at": 10, "state": "pending", "args_sha256": "secret-hash",
        "rule_key": "stardust:external-write:send_message",
    })
    assert projected == {
        "approval_id": "apr-1", "tool_name": "send_message", "reason": "Send reply",
        "requested_at": 10, "state": "pending",
    }


def test_cancel_uses_product_state_contract(monkeypatch):
    monkeypatch.setattr(
        bt,
        "_cancel_task",
        lambda task_id: json.dumps({
            "ok": True, "kind": "background_task", "task_id": task_id,
            "state": "cancelled", "terminal": True, "waiting": False,
            "needs_user": False, "cancelled": True,
        }),
    )
    result = json.loads(registry.dispatch(
        "background_task", {"action": "cancel", "task_id": "t1"}
    ))
    assert result == {
        "ok": True,
        "kind": "background_task",
        "task_id": "t1",
        "state": "cancelled",
        "terminal": True,
        "waiting": False,
        "needs_user": False,
        "cancelled": True,
    }


def test_cancel_archives_real_durable_task(kanban_home):
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="Long task", assignee="default")
        assert kb.get_task(conn, task_id).status != "archived"

    result = json.loads(bt._cancel_task(task_id))
    assert result["ok"] is True
    assert result["cancelled"] is True
    assert result["state"] == "cancelled"
    assert result["terminal"] is True
    assert "status" not in result

    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, task_id).status == "archived"


def test_cancel_is_idempotent_for_already_archived_task(kanban_home):
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="Cancel twice", assignee="default")
        assert kb.archive_task(conn, task_id)

    result = json.loads(bt._cancel_task(task_id))
    assert result["ok"] is True
    assert result["state"] == "cancelled"
    assert result["cancelled"] is False
    assert result["already_cancelled"] is True


def test_invalid_background_task_requests_fail_before_dispatch(monkeypatch):
    monkeypatch.setattr(
        bt,
        "_dispatch_kanban",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    missing_title = json.loads(registry.dispatch("background_task", {"action": "start"}))
    missing_id = json.loads(registry.dispatch("background_task", {"action": "status"}))
    missing_cancel_id = json.loads(registry.dispatch("background_task", {"action": "cancel"}))
    missing_approval_id = json.loads(registry.dispatch("background_task", {"action": "approve"}))
    missing_comment = json.loads(registry.dispatch(
        "background_task", {"action": "comment", "task_id": "t1"}
    ))
    bad_state = json.loads(registry.dispatch(
        "background_task", {"action": "list", "state": "blocked"}
    ))

    assert "title" in missing_title["error"]
    assert "task_id" in missing_id["error"]
    assert "task_id" in missing_cancel_id["error"]
    assert "task_id" in missing_approval_id["error"]
    assert "body" in missing_comment["error"]
    assert "state" in bad_state["error"]


def test_background_task_permission_semantics_match_action():
    for action in ("status", "list", "approvals"):
        assert permissions.classify_tool_permission(
            "background_task", {"action": action}
        ).level == permissions.ALLOW
    for action in ("start", "approve", "deny", "comment", "resume", "cancel"):
        assert permissions.classify_tool_permission(
            "background_task", {"action": action}
        ).level == permissions.NOTIFY


def test_assistant_orchestration_toolset_contains_background_task():
    assert "background_task" in resolve_toolset("assistant_orchestration")


def test_schema_teaches_three_way_routing_without_kanban_vocabulary():
    description = bt.BACKGROUND_TASK_SCHEMA["description"]
    properties = bt.BACKGROUND_TASK_SCHEMA["parameters"]["properties"]
    assert "ordinary questions directly" in description
    assert "finish in this live turn" in description
    assert "scheduled/recurring" in description
    assert "waiting_confirmation" in description
    assert "kanban" not in description.lower()
    assert "state" in properties
    assert "status" not in properties
    assert "failed" in properties["state"]["enum"]


def test_configured_default_assignee_wins_over_active_profile(monkeypatch):
    monkeypatch.setattr(bt, "load_config_readonly", lambda: {"kanban": {"default_assignee": "worker"}})
    monkeypatch.setattr(bt, "_current_profile_name", lambda: "desktop-main")
    assert bt._default_assignee() == "worker"


def test_active_profile_is_fallback_when_no_orchestration_default(monkeypatch):
    monkeypatch.setattr(bt, "load_config_readonly", lambda: {"kanban": {"default_assignee": ""}})
    monkeypatch.setattr(bt, "_current_profile_name", lambda: "desktop-main")
    assert bt._default_assignee() == "desktop-main"
