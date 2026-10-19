"""Atomic cron manual-run snapshot fencing."""

import pytest


@pytest.fixture()
def tmp_cron_store(tmp_path, monkeypatch):
    import cron.jobs as jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")
    return jobs


def test_trigger_job_accepts_unchanged_reviewed_snapshot(tmp_cron_store):
    jobs = tmp_cron_store
    created = jobs.create_job(prompt="Original task", schedule="every 1h")
    expected = jobs.get_job(created["id"])

    fired = jobs.trigger_job(created["id"], extra_prompt="approved context", expected=expected)

    assert fired is not None
    assert fired["manual_run_prompt"] == "approved context"
    assert fired["manual_run_at"]
    assert fired["enabled"] is True


def test_trigger_job_rejects_snapshot_after_edit_without_mutating_fire_fields(tmp_cron_store):
    jobs = tmp_cron_store
    created = jobs.create_job(prompt="Original task", schedule="every 1h")
    expected = jobs.get_job(created["id"])
    jobs.update_job(created["id"], {"prompt": "Changed task"})
    before = jobs.get_job(created["id"])

    assert jobs.trigger_job(created["id"], expected=expected) is None

    after = jobs.get_job(created["id"])
    assert after == before
    assert after["prompt"] == "Changed task"
    assert not after.get("manual_run_at")
    assert not after.get("manual_run_prompt")


def test_trigger_job_rejects_snapshot_after_pause(tmp_cron_store):
    jobs = tmp_cron_store
    created = jobs.create_job(prompt="Original task", schedule="every 1h")
    expected = jobs.get_job(created["id"])
    jobs.pause_job(created["id"])
    before = jobs.get_job(created["id"])

    assert jobs.trigger_job(created["id"], expected=expected) is None
    assert jobs.get_job(created["id"]) == before
    assert jobs.get_job(created["id"])["enabled"] is False
