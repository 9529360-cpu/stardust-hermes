"""Event templates (startup and folder triggers) in the blueprint catalog.

The trigger rules themselves are tested in test_triggers_model.py. These tests cover what a
template adds: the trigger it produces must pass cron.triggers.normalize_trigger, a folder slot
is validated as a form field, the Downloads default is resolved when it is used, and the
/blueprint agent seed does not send an event template down the clock-job path.
"""

import importlib
import os

import pytest

from cron.blueprint_catalog import (
    CATALOG,
    BlueprintFillError,
    blueprint_catalog_entry,
    blueprint_form_schema,
    fill_blueprint,
    get_blueprint,
)
from cron.triggers import normalize_trigger

EVENT_KEYS = ["startup-briefing", "new-pdf-summary", "downloads-report"]


def _folder_slot(blueprint):
    return next((slot for slot in blueprint.slots if slot.type == "folder"), None)


def test_event_templates_are_catalog_entries_with_a_trigger():
    keys = {r.key for r in CATALOG if r.trigger is not None}
    assert set(EVENT_KEYS) <= keys
    for key in EVENT_KEYS:
        blueprint = get_blueprint(key)
        assert blueprint.schedule_template == ""
        assert blueprint.trigger["type"] in {"startup", "folder"}


@pytest.mark.parametrize("key", EVENT_KEYS)
def test_event_template_trigger_passes_normalize_trigger(key, tmp_path):
    blueprint = get_blueprint(key)
    values = {"folder": str(tmp_path)} if _folder_slot(blueprint) else {}

    spec = fill_blueprint(blueprint, values)

    assert "schedule" not in spec, "an event job takes no schedule"
    assert normalize_trigger(spec["trigger"]) == spec["trigger"]


def test_startup_template_produces_a_startup_trigger():
    spec = fill_blueprint(get_blueprint("startup-briefing"), {})
    assert spec["trigger"] == {"type": "startup"}


def test_pdf_template_watches_only_pdf_files_in_the_chosen_folder(tmp_path):
    spec = fill_blueprint(get_blueprint("new-pdf-summary"), {"folder": str(tmp_path)})
    assert spec["trigger"]["type"] == "folder"
    assert spec["trigger"]["pattern"] == "*.pdf"
    assert spec["trigger"]["watch_dir"] == os.path.abspath(str(tmp_path))


def test_downloads_template_watches_every_file_in_the_chosen_folder(tmp_path):
    spec = fill_blueprint(get_blueprint("downloads-report"), {"folder": str(tmp_path)})
    assert spec["trigger"]["pattern"] == "*"
    assert spec["trigger"]["watch_dir"] == os.path.abspath(str(tmp_path))


def test_missing_folder_is_a_field_error_on_the_pdf_template():
    with pytest.raises(BlueprintFillError, match="folder"):
        fill_blueprint(get_blueprint("new-pdf-summary"), {"deliver": "local"})


def test_relative_folder_is_a_field_error(tmp_path):
    with pytest.raises(BlueprintFillError, match="absolute"):
        fill_blueprint(get_blueprint("new-pdf-summary"), {"folder": "inbox"})


def test_missing_folder_on_disk_is_a_field_error(tmp_path):
    with pytest.raises(BlueprintFillError, match="not an existing folder"):
        fill_blueprint(get_blueprint("new-pdf-summary"), {"folder": str(tmp_path / "nowhere")})


@pytest.mark.parametrize("folder", ["C:\\Windows", "C:\\"])
def test_system_folder_is_a_field_error_not_a_server_error(folder):
    # The deny list is text-based, so this holds on every OS.
    with pytest.raises(BlueprintFillError, match="Cannot watch this folder"):
        fill_blueprint(get_blueprint("new-pdf-summary"), {"folder": folder})


@pytest.fixture
def home_with_downloads(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_downloads_default_is_not_stored_as_a_fixed_path(home_with_downloads):
    slot = _folder_slot(get_blueprint("downloads-report"))
    assert slot.default is None
    assert slot.resolve_default is not None


def test_downloads_default_resolves_when_the_form_is_built(home_with_downloads):
    schema = blueprint_form_schema(get_blueprint("downloads-report"))
    folder_field = next(f for f in schema["fields"] if f["name"] == "folder")
    assert folder_field["type"] == "folder"
    assert folder_field["default"] == os.path.join(str(home_with_downloads), "Downloads")


def test_downloads_default_resolves_when_a_job_is_created(home_with_downloads):
    spec = fill_blueprint(get_blueprint("downloads-report"), {})
    assert spec["trigger"]["watch_dir"] == os.path.abspath(
        os.path.join(str(home_with_downloads), "Downloads")
    )


def test_catalog_entry_reports_the_trigger_type():
    assert blueprint_catalog_entry(get_blueprint("startup-briefing"))["trigger_type"] == "startup"
    assert blueprint_catalog_entry(get_blueprint("new-pdf-summary"))["trigger_type"] == "folder"
    assert blueprint_catalog_entry(get_blueprint("morning-brief"))["trigger_type"] == "schedule"


def test_agent_seed_for_an_event_template_does_not_ask_for_a_cron_expression():
    from hermes_cli.blueprint_cmd import build_blueprint_seed

    event_seed = build_blueprint_seed(get_blueprint("startup-briefing"))
    clock_seed = build_blueprint_seed(get_blueprint("morning-brief"))

    assert "cron expression" not in event_seed
    assert "trigger" in event_seed
    assert "cron expression" in clock_seed


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".stardust"
    home.mkdir()
    monkeypatch.setenv("STARDUST_HOME", str(home))
    import hermes_constants
    importlib.reload(hermes_constants)
    import cron.jobs as jobs
    importlib.reload(jobs)
    return jobs


def test_inline_event_blueprint_creates_a_trigger_job(isolated_home):
    from hermes_cli.blueprint_cmd import handle_blueprint_command

    res = handle_blueprint_command("startup-briefing deliver=local")

    assert "Scheduled" in res.text
    jobs = isolated_home.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == {"type": "startup"}
    assert jobs[0]["schedule"]["kind"] == "startup"
