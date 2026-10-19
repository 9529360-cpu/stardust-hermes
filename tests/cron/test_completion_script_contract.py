"""Persistence and invariant coverage for independent goal completion verification."""

import pytest


@pytest.fixture()
def tmp_cron_store(tmp_path, monkeypatch):
    import cron.jobs as jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")
    return jobs


def test_create_persists_completion_script_only_for_goal_mode(tmp_cron_store):
    jobs = tmp_cron_store

    job = jobs.create_job(
        prompt="Track until verified.",
        schedule="every 1h",
        stop_when_done=True,
        completion_script="verify_done.py",
    )

    assert job["completion_script"] == "verify_done.py"
    assert jobs.get_job(job["id"])["completion_script"] == "verify_done.py"


def test_create_rejects_completion_script_without_goal_mode(tmp_cron_store):
    jobs = tmp_cron_store

    with pytest.raises(ValueError, match="completion_script requires stop_when_done=True"):
        jobs.create_job(
            prompt="Ordinary recurring report.",
            schedule="every 1h",
            completion_script="verify_done.py",
        )


def test_disabling_goal_mode_requires_clearing_completion_script(tmp_cron_store):
    jobs = tmp_cron_store
    job = jobs.create_job(
        prompt="Track until verified.",
        schedule="every 1h",
        stop_when_done=True,
        completion_script="verify_done.py",
    )

    with pytest.raises(ValueError, match="completion_script requires stop_when_done=True"):
        jobs.update_job(job["id"], {"stop_when_done": False})

    updated = jobs.update_job(job["id"], {
        "stop_when_done": False,
        "completion_script": "",
    })
    assert updated is not None
    assert "stop_when_done" not in updated
    assert "completion_script" not in updated


def test_memory_scope_and_completion_script_roundtrip_together(tmp_cron_store):
    jobs = tmp_cron_store

    job = jobs.create_job(
        prompt="Private bounded task.",
        schedule="every 2h",
        stop_when_done=True,
        completion_script="verify_done.py",
        memory_scope="none",
    )

    stored = jobs.get_job(job["id"])
    assert stored["completion_script"] == "verify_done.py"
    assert stored["memory_scope"] == "none"
