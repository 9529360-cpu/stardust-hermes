"""Store behaviour of event triggers: creation, updates, the due scan, run-now and run outcomes.

A job without a trigger must keep its exact record shape and schedule behaviour. The schedule
test below asserts that the new fields are absent rather than asserting new behaviour.
"""

import pytest


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("STARDUST_HOME", str(home))
    yield home


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def _startup(name="brief"):
    from cron.jobs import create_job

    return create_job(prompt="Summarise today", schedule=None, name=name,
                      trigger={"type": "startup"})


def _folder(inbox, **extra):
    from cron.jobs import create_job

    trigger = {"type": "folder", "watch_dir": str(inbox), **extra}
    return create_job(prompt="Sort the new file", schedule=None, name="sorter", trigger=trigger)


class TestScheduleJobsAreUnchanged:
    def test_schedule_job_record_has_no_trigger_fields(self, temp_home):
        from cron.jobs import create_job, load_jobs

        job = create_job(prompt="Check mail", schedule="every 2h", name="mail")
        stored = next(item for item in load_jobs() if item["id"] == job["id"])
        assert "trigger" not in stored and "trigger_state" not in stored
        assert stored["schedule"]["kind"] == "interval"
        assert stored["next_run_at"] is not None

    def test_schedule_job_still_requires_a_schedule(self, temp_home):
        from cron.jobs import create_job

        with pytest.raises(ValueError, match="needs a schedule"):
            create_job(prompt="Check mail", schedule=None, name="mail")

    def test_explicit_schedule_trigger_is_a_plain_schedule_job(self, temp_home):
        from cron.jobs import create_job

        job = create_job(prompt="Check mail", schedule="every 2h", name="mail",
                         trigger={"type": "schedule"})
        assert "trigger" not in job and job["next_run_at"] is not None


class TestEventJobCreation:
    def test_startup_job_is_stored_without_a_clock(self, temp_home):
        from cron.jobs import get_due_jobs, get_job

        job = _startup()
        stored = get_job(job["id"])
        assert stored["trigger"] == {"type": "startup"}
        assert stored["schedule"] == {"kind": "startup", "display": "on Stardust start"}
        assert stored["next_run_at"] is None
        assert stored["enabled"] is True and stored["state"] == "scheduled"
        assert get_due_jobs() == []  # the clock never fires an event job

    def test_folder_job_is_baselined_at_creation(self, temp_home, inbox):
        from cron.jobs import load_jobs

        job = _folder(inbox, poll_seconds=45)
        stored = next(item for item in load_jobs() if item["id"] == job["id"])
        assert stored["trigger"]["poll_seconds"] == 45
        assert stored["trigger"]["pattern"] == "*"
        assert stored["trigger_state"]["floor"] > 0
        assert stored["trigger_state"]["seen"] == []

    def test_listing_shows_status_and_hides_runtime_state(self, temp_home, inbox):
        from cron.jobs import list_jobs

        _folder(inbox)
        listed = list_jobs()
        assert len(listed) == 1
        assert "trigger_state" not in listed[0]
        assert listed[0]["trigger_status"]["type"] == "folder"
        assert listed[0]["trigger_status"]["queued"] == 0
        assert listed[0]["trigger_status"]["last_triggered_at"] is None

    def test_denied_folder_is_refused_and_nothing_is_stored(self, temp_home):
        from cron.jobs import create_job, load_jobs

        for path in ("C:\\Windows", "/etc", "C:\\"):
            with pytest.raises(ValueError, match="Cannot watch this folder"):
                create_job(prompt="x", schedule=None, trigger={"type": "folder", "watch_dir": path})
        assert load_jobs() == []

    def test_event_job_cannot_also_take_a_schedule(self, temp_home):
        from cron.jobs import create_job, load_jobs

        with pytest.raises(ValueError, match="cannot also take a schedule"):
            create_job(prompt="x", schedule="every 1h", trigger={"type": "startup"})
        assert load_jobs() == []

    def test_unknown_trigger_fields_are_refused(self, temp_home):
        from cron.jobs import create_job

        with pytest.raises(ValueError, match="Unknown trigger field"):
            create_job(prompt="x", schedule=None, trigger={"type": "startup", "delay": 5})


class TestEventJobUpdates:
    def test_trigger_type_cannot_change(self, temp_home, inbox):
        from cron.jobs import update_job

        startup = _startup()
        with pytest.raises(ValueError, match="Cannot change a startup job to folder"):
            update_job(startup["id"], {"trigger": {"type": "folder", "watch_dir": str(inbox)}})
        folder = _folder(inbox)
        with pytest.raises(ValueError, match="Cannot change a folder job to schedule"):
            update_job(folder["id"], {"trigger": {"type": "schedule"}})

    def test_event_job_takes_no_schedule_on_update(self, temp_home):
        from cron.jobs import update_job

        job = _startup()
        with pytest.raises(ValueError, match="cannot take a schedule"):
            update_job(job["id"], {"schedule": "every 1h"})

    def test_runtime_state_cannot_be_written_directly(self, temp_home, inbox):
        from cron.jobs import update_job

        job = _folder(inbox)
        with pytest.raises(ValueError, match="cannot be updated: trigger_state"):
            update_job(job["id"], {"trigger_state": {"seen": [], "queue": []}})

    def test_denied_folder_is_refused_on_update(self, temp_home, inbox):
        from cron.jobs import get_job, update_job

        job = _folder(inbox)
        with pytest.raises(ValueError, match="Cannot watch this folder"):
            update_job(job["id"], {"trigger": {"type": "folder", "watch_dir": "/usr"}})
        assert get_job(job["id"])["trigger"]["watch_dir"] == str(inbox)

    def test_poll_change_keeps_memory_but_a_new_pattern_rebaselines(self, temp_home, inbox):
        from cron.jobs import load_jobs, save_jobs, update_job

        job = _folder(inbox)
        jobs = load_jobs()
        next(item for item in jobs if item["id"] == job["id"])["trigger_state"]["seen"] = [
            ["old.pdf", 3, 1.0]]
        save_jobs(jobs)

        def stored():
            return next(item for item in load_jobs() if item["id"] == job["id"])

        update_job(job["id"], {"trigger": {"type": "folder", "watch_dir": str(inbox), "poll_seconds": 90}})
        assert stored()["trigger_state"]["seen"] == [["old.pdf", 3, 1.0]]
        assert stored()["trigger"]["poll_seconds"] == 90

        update_job(job["id"], {"trigger": {"type": "folder", "watch_dir": str(inbox), "pattern": "*.pdf"}})
        assert stored()["trigger_state"]["seen"] == []
        assert stored()["schedule"]["display"].endswith("(*.pdf)")


class TestDueScanAndRuns:
    def test_run_now_makes_an_event_job_due_once(self, temp_home):
        from cron.jobs import get_due_jobs, get_job, mark_job_run, trigger_job

        job = _startup()
        trigger_job(job["id"])
        assert [due["id"] for due in get_due_jobs()] == [job["id"]]
        mark_job_run(job["id"], True)
        assert get_due_jobs() == []
        assert get_job(job["id"])["state"] == "scheduled"

    def test_successful_run_keeps_an_event_job_scheduled(self, temp_home):
        from cron.jobs import get_job, mark_job_run

        job = _startup()
        assert mark_job_run(job["id"], True) is True
        stored = get_job(job["id"])
        assert stored["enabled"] is True
        assert stored["state"] == "scheduled"
        assert stored["next_run_at"] is None
        assert stored["last_status"] == "ok"

    def test_pause_and_resume_work_for_event_jobs(self, temp_home):
        from cron.jobs import get_job, pause_job, resume_job

        job = _startup()
        assert pause_job(job["id"])["enabled"] is False
        resumed = resume_job(job["id"])
        assert resumed["enabled"] is True and resumed["state"] == "scheduled"
        assert get_job(job["id"])["next_run_at"] is None

    def test_removing_an_event_job_removes_its_state(self, temp_home):
        from cron.jobs import load_jobs, remove_job

        job = _startup()
        assert remove_job(job["id"]) is True
        assert load_jobs() == []

    def test_a_stale_next_run_on_an_event_job_is_not_due_by_the_clock(self, temp_home):
        from cron.jobs import get_due_jobs, load_jobs, save_jobs

        job = _startup()
        jobs = load_jobs()
        next(item for item in jobs if item["id"] == job["id"])["next_run_at"] = "2020-01-01T00:00:00+00:00"
        save_jobs(jobs)  # a hand-edited or stale record must still never fire by the clock
        assert get_due_jobs() == []
