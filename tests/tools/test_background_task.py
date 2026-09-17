import json

from hermes_cli import assistant_permissions as permissions
from tools import background_task as bt
from tools.registry import registry


def test_start_defaults_assignee_and_surfaces_delivery_truth(monkeypatch):
    seen = {}
    monkeypatch.setattr(bt, "_default_assignee", lambda: "desktop-main")

    def dispatch(name, payload, kwargs):
        seen.update(name=name, payload=dict(payload), kwargs=dict(kwargs))
        return json.dumps({
            "ok": True,
            "task_id": "bg-1",
            "status": "running",
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
    assert result["subscribed"] is True
    assert result["notification_mode"] == "automatic"
    assert result["kind"] == "background_task"


def test_start_preserves_explicit_specialist_and_manual_delivery(monkeypatch):
    seen = {}
    monkeypatch.setattr(bt, "_default_assignee", lambda: "should-not-be-used")

    def dispatch(name, payload, kwargs):
        seen.update(name=name, payload=dict(payload))
        return json.dumps({"ok": True, "task_id": "bg-2", "subscribed": False})

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)
    result = json.loads(registry.dispatch(
        "background_task",
        {"action": "start", "title": "Review code", "assignee": "reviewer"},
    ))

    assert seen["name"] == "kanban_create"
    assert seen["payload"]["assignee"] == "reviewer"
    assert result["notification_mode"] == "manual"


def test_read_and_mutation_actions_route_to_existing_kanban_handlers(monkeypatch):
    calls = []

    def dispatch(name, payload, kwargs):
        calls.append((name, dict(payload)))
        return json.dumps({"ok": True})

    monkeypatch.setattr(bt, "_dispatch_kanban", dispatch)

    registry.dispatch("background_task", {"action": "status", "task_id": "t1"})
    registry.dispatch("background_task", {"action": "list", "status": "running", "limit": 5})
    registry.dispatch("background_task", {"action": "comment", "task_id": "t1", "body": "note"})
    registry.dispatch("background_task", {"action": "resume", "task_id": "t1"})

    assert calls == [
        ("kanban_show", {"task_id": "t1"}),
        ("kanban_list", {"status": "running", "limit": 5}),
        ("kanban_comment", {"task_id": "t1", "body": "note"}),
        ("kanban_unblock", {"task_id": "t1"}),
    ]


def test_invalid_background_task_requests_fail_before_dispatch(monkeypatch):
    monkeypatch.setattr(
        bt,
        "_dispatch_kanban",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    missing_title = json.loads(registry.dispatch("background_task", {"action": "start"}))
    missing_id = json.loads(registry.dispatch("background_task", {"action": "status"}))
    missing_comment = json.loads(registry.dispatch(
        "background_task", {"action": "comment", "task_id": "t1"}
    ))

    assert "title" in missing_title["error"]
    assert "task_id" in missing_id["error"]
    assert "body" in missing_comment["error"]


def test_background_task_permission_semantics_match_action():
    assert permissions.classify_tool_permission(
        "background_task", {"action": "status"}
    ).level == permissions.ALLOW
    assert permissions.classify_tool_permission(
        "background_task", {"action": "list"}
    ).level == permissions.ALLOW
    for action in ("start", "comment", "resume"):
        assert permissions.classify_tool_permission(
            "background_task", {"action": action}
        ).level == permissions.NOTIFY


def test_configured_default_assignee_wins_over_active_profile(monkeypatch):
    monkeypatch.setattr(bt, "load_config_readonly", lambda: {"kanban": {"default_assignee": "worker"}})
    monkeypatch.setattr(bt, "_current_profile_name", lambda: "desktop-main")
    assert bt._default_assignee() == "worker"


def test_active_profile_is_fallback_when_no_orchestration_default(monkeypatch):
    monkeypatch.setattr(bt, "load_config_readonly", lambda: {"kanban": {"default_assignee": ""}})
    monkeypatch.setattr(bt, "_current_profile_name", lambda: "desktop-main")
    assert bt._default_assignee() == "desktop-main"
