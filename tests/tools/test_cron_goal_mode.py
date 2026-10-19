"""Tool-facing coverage for cron goal-task mode."""

import json


def test_schema_exposes_stop_when_done_as_opt_in_boolean():
    from tools.cronjob_tools import CRONJOB_SCHEMA

    prop = CRONJOB_SCHEMA["parameters"]["properties"]["stop_when_done"]
    assert prop["type"] == "boolean"
    assert "bounded recurring follow-ups" in prop["description"]


def test_model_handler_forwards_stop_when_done(monkeypatch):
    import tools.cronjob_tools as tools

    captured = {}

    def fake_cronjob(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True})

    monkeypatch.setattr(tools, "cronjob", fake_cronjob)

    result = json.loads(tools._cronjob_handler(
        {"action": "create", "stop_when_done": True},
        session_id="session-1",
    ))

    assert result["success"] is True
    assert captured["stop_when_done"] is True
    assert captured["session_id"] == "session-1"


def test_job_view_surfaces_goal_mode():
    from tools.cronjob_job_args import _format_job

    view = _format_job({
        "id": "goal-1",
        "prompt": "Track the package.",
        "schedule_display": "every 1h",
        "stop_when_done": True,
    })

    assert view["stop_when_done"] is True


def test_schema_exposes_completion_verifier_and_memory_scope():
    from tools.cronjob_tools import CRONJOB_SCHEMA

    props = CRONJOB_SCHEMA["parameters"]["properties"]
    assert props["completion_script"]["type"] == "string"
    assert props["memory_scope"]["enum"] == ["profile", "none"]


def test_model_handler_forwards_completion_verifier_and_memory_scope(monkeypatch):
    import tools.cronjob_tools as tools

    captured = {}

    def fake_cronjob(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True})

    monkeypatch.setattr(tools, "cronjob", fake_cronjob)
    result = json.loads(tools._cronjob_handler(
        {
            "action": "create",
            "completion_script": "verify_done.py",
            "memory_scope": "none",
        },
        session_id="session-2",
    ))

    assert result["success"] is True
    assert captured["completion_script"] == "verify_done.py"
    assert captured["memory_scope"] == "none"


def test_job_view_surfaces_completion_verifier_and_memory_scope():
    from tools.cronjob_job_args import _format_job

    view = _format_job({
        "id": "goal-2",
        "prompt": "Track the package.",
        "schedule_display": "every 1h",
        "stop_when_done": True,
        "completion_script": "verify_done.py",
        "memory_scope": "none",
    })

    assert view["completion_script"] == "verify_done.py"
    assert view["memory_scope"] == "none"
