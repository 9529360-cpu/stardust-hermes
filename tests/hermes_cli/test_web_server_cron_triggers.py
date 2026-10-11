"""Dashboard REST contract for event triggers (startup and folder jobs).

Create and update accept a trigger, a value the store refuses comes back as a readable HTTP 400,
the blueprint catalog advertises the event templates, and a blueprint can create an event job.
The trigger rules are tested in tests/cron/test_triggers_model.py.
"""

import os

import pytest
from fastapi import HTTPException

import hermes_cli.web_models as _web_models
import hermes_cli.web_routers.cron as _rt_cron


@pytest.fixture()
def isolated_profiles(tmp_path, monkeypatch):
    """Give profile discovery an isolated default home with one named profile."""
    from hermes_cli import profiles

    default_home = tmp_path / ".stardust"
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"

    for home in (default_home, worker_home):
        (home / "cron").mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"default": default_home, "worker_alpha": worker_home}


async def _listed(job_id: str):
    jobs = await _rt_cron.list_cron_jobs(profile="worker_alpha")
    return next(job for job in jobs if job["id"] == job_id)


@pytest.mark.asyncio
async def test_create_startup_job_stores_the_trigger_and_no_clock(isolated_profiles):
    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(prompt="Brief me on today.", trigger={"type": "startup"}),
        profile="worker_alpha",
    )

    assert job["trigger"] == {"type": "startup"}
    assert job["schedule"]["kind"] == "startup"
    assert job["next_run_at"] is None

    listed = await _listed(job["id"])
    assert listed["trigger_status"]["type"] == "startup"
    assert listed["trigger_status"]["last_triggered_at"] is None


@pytest.mark.asyncio
async def test_create_folder_job_stores_the_watch_folder_and_pattern(isolated_profiles, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(
            prompt="Summarise the new PDF.",
            trigger={"type": "folder", "watch_dir": str(inbox), "pattern": "*.pdf"},
        ),
        profile="worker_alpha",
    )

    assert job["trigger"]["type"] == "folder"
    assert job["trigger"]["watch_dir"] == os.path.abspath(str(inbox))
    assert job["trigger"]["pattern"] == "*.pdf"
    assert job["trigger"]["poll_seconds"] == 60
    assert job["schedule"]["kind"] == "folder"


@pytest.mark.asyncio
@pytest.mark.parametrize("watch_dir", ["C:\\Windows", "C:\\", "/etc"])
async def test_create_folder_job_refuses_a_system_folder_with_a_readable_400(isolated_profiles, watch_dir):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.create_cron_job(
            _web_models.CronJobCreate(prompt="x", trigger={"type": "folder", "watch_dir": watch_dir}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Cannot watch this folder: ")


@pytest.mark.asyncio
async def test_create_rejects_an_unknown_trigger_type_with_a_400(isolated_profiles):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.create_cron_job(
            _web_models.CronJobCreate(prompt="x", trigger={"type": "webhook"}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "Invalid trigger type" in exc.value.detail


@pytest.mark.asyncio
async def test_create_rejects_a_non_object_trigger_with_a_400(isolated_profiles):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.create_cron_job(
            _web_models.CronJobCreate(prompt="x", trigger="startup"),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "trigger must be an object" in exc.value.detail


@pytest.mark.asyncio
async def test_create_event_job_with_a_schedule_is_a_400(isolated_profiles):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.create_cron_job(
            _web_models.CronJobCreate(prompt="x", schedule="every 1h", trigger={"type": "startup"}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "cannot also take a schedule" in exc.value.detail


@pytest.mark.asyncio
async def test_create_clock_job_without_a_schedule_is_a_400(isolated_profiles):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.create_cron_job(_web_models.CronJobCreate(prompt="x"), profile="worker_alpha")

    assert exc.value.status_code == 400
    assert "needs a schedule" in exc.value.detail


@pytest.mark.asyncio
async def test_update_changes_the_watch_folder_and_pattern_of_a_folder_job(isolated_profiles, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(
            prompt="x", trigger={"type": "folder", "watch_dir": str(first), "pattern": "*.pdf"}),
        profile="worker_alpha",
    )

    updated = await _rt_cron.update_cron_job(
        job["id"],
        _web_models.CronJobUpdate(
            updates={"trigger": {"type": "folder", "watch_dir": str(second), "pattern": "*.txt"}}),
        profile="worker_alpha",
    )

    assert updated["trigger"]["watch_dir"] == os.path.abspath(str(second))
    assert updated["trigger"]["pattern"] == "*.txt"


@pytest.mark.asyncio
async def test_update_cannot_switch_a_folder_job_to_startup(isolated_profiles, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(prompt="x", trigger={"type": "folder", "watch_dir": str(inbox)}),
        profile="worker_alpha",
    )

    with pytest.raises(HTTPException) as exc:
        await _rt_cron.update_cron_job(
            job["id"],
            _web_models.CronJobUpdate(updates={"trigger": {"type": "startup"}}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "Cannot change a folder job to startup" in exc.value.detail


@pytest.mark.asyncio
async def test_update_refuses_a_schedule_on_an_event_job(isolated_profiles):
    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(prompt="x", trigger={"type": "startup"}),
        profile="worker_alpha",
    )

    with pytest.raises(HTTPException) as exc:
        await _rt_cron.update_cron_job(
            job["id"],
            _web_models.CronJobUpdate(updates={"schedule": "every 1h"}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "cannot take a schedule" in exc.value.detail


@pytest.mark.asyncio
async def test_update_refuses_a_system_folder_on_a_folder_job(isolated_profiles, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    job = await _rt_cron.create_cron_job(
        _web_models.CronJobCreate(prompt="x", trigger={"type": "folder", "watch_dir": str(inbox)}),
        profile="worker_alpha",
    )

    with pytest.raises(HTTPException) as exc:
        await _rt_cron.update_cron_job(
            job["id"],
            _web_models.CronJobUpdate(updates={"trigger": {"type": "folder", "watch_dir": "C:\\Windows"}}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 400
    assert "Cannot watch this folder" in exc.value.detail


@pytest.mark.asyncio
async def test_blueprint_catalog_advertises_event_templates_with_a_folder_field(isolated_profiles):
    listing = await _rt_cron.list_cron_blueprints()
    by_key = {entry["key"]: entry for entry in listing["blueprints"]}

    assert by_key["startup-briefing"]["trigger_type"] == "startup"
    assert by_key["morning-brief"]["trigger_type"] == "schedule"
    folder_fields = [f for f in by_key["new-pdf-summary"]["fields"] if f["type"] == "folder"]
    assert [f["name"] for f in folder_fields] == ["folder"]


@pytest.mark.asyncio
async def test_blueprint_instantiate_creates_a_folder_job(isolated_profiles, tmp_path):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()

    job = await _rt_cron.instantiate_blueprint(
        _web_models.AutomationBlueprintInstantiate(
            blueprint="new-pdf-summary", values={"folder": str(pdfs), "deliver": "local"}),
        profile="worker_alpha",
    )

    assert job["trigger"]["pattern"] == "*.pdf"
    assert job["schedule"]["kind"] == "folder"


@pytest.mark.asyncio
async def test_blueprint_instantiate_without_a_folder_is_a_422(isolated_profiles):
    with pytest.raises(HTTPException) as exc:
        await _rt_cron.instantiate_blueprint(
            _web_models.AutomationBlueprintInstantiate(
                blueprint="new-pdf-summary", values={"deliver": "local"}),
            profile="worker_alpha",
        )

    assert exc.value.status_code == 422
    assert "folder" in exc.value.detail
