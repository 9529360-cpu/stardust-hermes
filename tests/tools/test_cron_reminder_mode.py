"""Agent-facing deterministic reminder mode."""

import json


def test_schema_exposes_durable_reminder_mode():
    from tools.cronjob_tools import CRONJOB_SCHEMA

    prop = CRONJOB_SCHEMA["parameters"]["properties"]["reminder"]
    assert prop["type"] == "boolean"
    assert "one-shot" in prop["description"]
    assert "no model" in prop["description"].lower()


def test_model_handler_forwards_reminder(monkeypatch):
    import tools.cronjob_tools as tools

    captured = {}

    def fake_cronjob(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True})

    monkeypatch.setattr(tools, "cronjob", fake_cronjob)
    result = json.loads(tools._cronjob_handler(
        {"action": "create", "reminder": True},
        session_id="chat-1",
    ))

    assert result["success"] is True
    assert captured["reminder"] is True


def test_create_captures_local_origin_and_wires_reminder(monkeypatch):
    import cron.scheduler as scheduler
    import cron.session_return as session_return
    import tools.cronjob_tools as tools

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "id": "reminder-1",
            "name": "Passport reminder",
            "prompt": kwargs["prompt"],
            "skills": [],
            "schedule": {"kind": "once", "run_at": "2099-01-01T09:00:00+00:00"},
            "schedule_display": "once at 2099-01-01 09:00",
            "repeat": {"times": 1, "completed": 0},
            "deliver": kwargs["deliver"],
            "next_run_at": "2099-01-01T09:00:00+00:00",
            "enabled": True,
            "state": "scheduled",
            "reminder": True,
            "local_session_origin": kwargs["local_session_origin"],
        }

    monkeypatch.setattr(
        session_return,
        "capture_local_session_origin",
        lambda _deliver, _session_id=None: {"source": "desktop", "session_id": "chat-1"},
    )
    monkeypatch.setattr(scheduler, "create_job_with_scheduler_registration", fake_create)
    monkeypatch.setattr(tools, "_gateway_liveness_notice", lambda **_kwargs: {})

    result = json.loads(tools.cronjob(
        action="create",
        prompt="明天九点去办护照。",
        schedule="2099-01-01T09:00:00+00:00",
        name="Passport reminder",
        reminder=True,
        session_id="chat-1",
    ))

    assert result["success"] is True
    assert captured["reminder"] is True
    assert captured["local_session_origin"] == {"source": "desktop", "session_id": "chat-1"}


def test_create_reminder_fails_without_persisted_local_conversation(monkeypatch):
    import cron.session_return as session_return
    import tools.cronjob_tools as tools

    monkeypatch.setattr(
        session_return, "capture_local_session_origin", lambda *_args, **_kwargs: None)

    result = json.loads(tools.cronjob(
        action="create",
        prompt="提醒我。",
        schedule="2099-01-01T09:00:00+00:00",
        reminder=True,
        session_id="chat-1",
    ))

    assert result["success"] is False
    assert "Desktop/TUI" in result["error"]


def test_dry_run_reminder_validates_with_captured_origin(monkeypatch):
    import cron.session_return as session_return
    import tools.cronjob_tools as tools

    monkeypatch.setattr(
        session_return,
        "capture_local_session_origin",
        lambda *_args, **_kwargs: {"source": "tui", "session_id": "chat-2"},
    )

    result = json.loads(tools.cronjob(
        action="create",
        prompt="提醒我喝水。",
        schedule="2099-01-01T09:00:00+00:00",
        reminder=True,
        session_id="chat-2",
        dry_run=True,
    ))

    assert result["success"] is True
    assert result["dry_run"] is True
