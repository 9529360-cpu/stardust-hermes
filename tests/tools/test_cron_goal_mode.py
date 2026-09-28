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
