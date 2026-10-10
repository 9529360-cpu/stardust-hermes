"""A routine requested in chat is previewed before anything is saved (work-ledger Gap 7).

``parse_nl_schedule`` gives the parsed schedule, its display and the next run times. The runs are
chained with the scheduler's own ``compute_next_run``, so the preview cannot disagree with what the
job will do. ``cronjob(..., dry_run=True)`` runs every check a real create or schedule update runs,
but it writes nothing and registers nothing with the scheduler.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    """Isolate HERMES_HOME per test and reload the modules that cache it."""
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    (home / "scripts").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    import hermes_constants

    importlib.reload(hermes_constants)
    import cron.jobs

    importlib.reload(cron.jobs)
    import cron.jobs_schedule

    importlib.reload(cron.jobs_schedule)
    import cron.scheduler

    importlib.reload(cron.scheduler)
    return home


@pytest.fixture
def registrations(monkeypatch):
    """Record every scheduler registration. A dry run must record none."""
    calls: list = []

    def _record(job):
        calls.append(job["id"])
        return job

    monkeypatch.setattr("cron.scheduler.register_persisted_job", _record)
    return calls


def test_weekly_phrase_previews_three_monday_nine_am_runs(hermes_env):
    from cron.jobs_schedule import parse_nl_schedule

    preview = parse_nl_schedule("every monday 9am")

    assert preview["schedule"]["kind"] == "cron"
    runs = [datetime.fromisoformat(run) for run in preview["next_runs"]]
    assert len(runs) == 3
    assert all(run.weekday() == 0 and (run.hour, run.minute) == (9, 0) for run in runs)
    assert runs == sorted(runs) and len(set(runs)) == 3


def test_interval_preview_spaces_runs_by_the_interval(hermes_env):
    from cron.jobs_schedule import parse_nl_schedule

    runs = [datetime.fromisoformat(run) for run in parse_nl_schedule("every 2h")["next_runs"]]

    assert len(runs) == 3
    assert {(later - earlier).total_seconds() for earlier, later in zip(runs, runs[1:])} == {7200.0}


def test_one_shot_preview_has_one_run_and_bad_input_is_readable(hermes_env):
    from cron.jobs_schedule import parse_nl_schedule

    once = parse_nl_schedule("in 2h")

    assert once["schedule"]["kind"] == "once"
    assert len(once["next_runs"]) == 1
    with pytest.raises(ValueError, match="Invalid schedule"):
        parse_nl_schedule("sometime soon")


def test_dry_run_create_previews_and_saves_nothing(hermes_env, registrations):
    from cron.jobs import load_jobs
    from tools.cronjob_tools import cronjob

    before = load_jobs()
    result = json.loads(cronjob(
        action="create", schedule="every monday 9am", prompt="Summarize the news",
        deliver="local", dry_run=True))

    assert result["success"] is True
    assert result["dry_run"] is True and result["saved"] is False
    assert len(result["preview"]["next_runs"]) == 3
    assert result["preview"]["deliver"] == "local"
    assert "id" not in result["preview"]
    assert load_jobs() == before
    assert registrations == []


def test_dry_run_with_bad_schedule_is_a_readable_error_and_saves_nothing(hermes_env, registrations):
    from cron.jobs import load_jobs
    from tools.cronjob_tools import cronjob

    result = json.loads(cronjob(
        action="create", schedule="sometime soon", prompt="Summarize the news",
        deliver="local", dry_run=True))

    assert result["success"] is False
    assert "Invalid schedule" in json.dumps(result)
    assert load_jobs() == []
    assert registrations == []


def test_real_create_still_saves_and_registers_after_a_preview(hermes_env, registrations):
    from cron.jobs import load_jobs
    from tools.cronjob_tools import cronjob

    preview = json.loads(cronjob(
        action="create", schedule="every monday 9am", prompt="Summarize the news",
        deliver="local", dry_run=True))
    assert preview["saved"] is False
    assert load_jobs() == []

    created = json.loads(cronjob(
        action="create", schedule="every monday 9am", prompt="Summarize the news", deliver="local"))

    assert created["success"] is True
    assert len(load_jobs()) == 1
    assert registrations == [load_jobs()[0]["id"]]


def test_update_dry_run_previews_a_schedule_change_without_changing_the_job(hermes_env, registrations):
    from cron.jobs import load_jobs
    from tools.cronjob_tools import cronjob

    created = json.loads(cronjob(
        action="create", schedule="every monday 9am", prompt="Summarize the news", deliver="local"))
    job_id = created["job"]["job_id"]
    stored_before = load_jobs()

    result = json.loads(cronjob(action="update", job_id=job_id, schedule="every 2h", dry_run=True))

    assert result["success"] is True
    assert result["dry_run"] is True and result["saved"] is False
    assert result["preview"]["previous_schedule"] == stored_before[0]["schedule_display"]
    assert len(result["preview"]["next_runs"]) == 3
    assert load_jobs() == stored_before
