"""Tests for the Suggested Cron Jobs feature.

Covers the store (add/dedup/cap/accept/dismiss/latch), catalog seeding, the
blueprint->suggestion bridge, and the shared command handler. Uses an isolated
HERMES_HOME so the real suggestions.json is never touched.
"""

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A cron.suggestions module bound to an isolated HERMES_HOME."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants
    importlib.reload(hermes_constants)
    import cron.suggestions as s
    importlib.reload(s)
    return s


def _add(store, key="k1", title="Test", source="catalog", schedule="0 9 * * *"):
    return store.add_suggestion(
        title=title,
        description="desc",
        source=source,
        job_spec={"prompt": "do it", "schedule": schedule, "name": title, "deliver": "origin"},
        dedup_key=key,
    )


class TestStore:
    def test_explicit_file_override_wins_over_profile_home(self, tmp_path, monkeypatch):
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )
        import cron.suggestions as suggestions_mod

        explicit_file = tmp_path / "explicit" / "suggestions.json"
        profile_home = tmp_path / "profile"
        monkeypatch.setattr(suggestions_mod, "SUGGESTIONS_FILE", explicit_file)

        token = set_hermes_home_override(profile_home)
        try:
            _add(suggestions_mod, key="explicit-file")
        finally:
            reset_hermes_home_override(token)

        assert explicit_file.exists()
        assert not (profile_home / "cron" / "suggestions.json").exists()

    def test_profile_override_routes_writes_to_current_home(self, tmp_path):
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )
        import cron.suggestions as suggestions_mod

        profile_a = tmp_path / "profile-a"
        profile_b = tmp_path / "profile-b"

        import_token = set_hermes_home_override(profile_a)
        try:
            importlib.reload(suggestions_mod)
        finally:
            reset_hermes_home_override(import_token)

        runtime_token = set_hermes_home_override(profile_b)
        try:
            _add(suggestions_mod, key="profile-b")
        finally:
            reset_hermes_home_override(runtime_token)

        assert (profile_b / "cron" / "suggestions.json").exists()
        assert not (profile_a / "cron" / "suggestions.json").exists()

    def test_mutation_fails_closed_without_cross_process_lock_backend(self, store, monkeypatch):
        import cron.jobs as cron_jobs

        monkeypatch.setattr(cron_jobs, "_acquire_flock", lambda _fd, _timeout: None)
        with pytest.raises(RuntimeError, match="cross-process suggestion locking is unavailable"):
            _add(store, key="no-flock")
        assert store.load_suggestions() == []

    def test_add_and_list_pending(self, store):
        rec = _add(store)
        assert rec is not None
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0]["title"] == "Test"
        assert pending[0]["status"] == "pending"

    def test_dedup_blocks_duplicate_pending(self, store):
        assert _add(store, key="dup") is not None
        assert _add(store, key="dup") is None  # same key already pending
        assert len(store.list_pending()) == 1

    def test_dismiss_latches_against_redisplay(self, store):
        _add(store, key="latch")
        assert store.dismiss_suggestion("1") is True
        assert store.list_pending() == []
        # Re-adding the same key is refused (never re-offer a dismissed one).
        assert _add(store, key="latch") is None

    def test_resolved_accept_cannot_be_overwritten_by_dismiss(self, store):
        rec = _add(store, key="accepted-immutable")
        assert rec is not None
        with patch("cron.jobs.create_job", lambda **kwargs: {"id": "accepted-job", **kwargs}):
            assert store.accept_suggestion(rec["id"]) is not None

        assert store.dismiss_suggestion(rec["id"]) is False
        assert store.get_suggestion(rec["id"])["status"] == "accepted"

    def test_accept_reconciles_job_committed_before_suggestion_resolution(self, store):
        rec = _add(store, key="crash-window", title="Crash Window")
        assert rec is not None
        existing = {
            "id": "already-durable",
            "name": "Crash Window",
            "enabled": True,
            "source_suggestion_id": rec["id"],
        }

        with (
            patch("cron.jobs.load_jobs", return_value=[existing]),
            patch("cron.scheduler.register_persisted_job", return_value=existing) as register,
            patch("cron.scheduler.create_job_with_scheduler_registration") as create,
        ):
            recovered = store.accept_suggestion(rec["id"])

        assert recovered is existing
        register.assert_called_once_with(existing)
        create.assert_not_called()
        assert store.get_suggestion(rec["id"])["status"] == "accepted"

    def test_unknown_source_rejected(self, store):
        with pytest.raises(ValueError):
            store.add_suggestion(title="x", description="d", source="bogus", job_spec={}, dedup_key="k")

    def test_usage_source_is_consent_first_self_improvement(self, store):
        """Background review suggestions must stay pending until user acceptance."""
        rec = _add(
            store,
            key="usage:weekly-summary",
            title="Weekly project summary",
            source="usage",
            schedule="0 17 * * 5",
        )

        assert rec is not None
        assert rec["source"] == "usage"
        assert rec["status"] == "pending"
        assert rec["job_spec"]["schedule"] == "0 17 * * 5"
        assert store.list_pending()[0]["dedup_key"] == "usage:weekly-summary"

    def test_pending_cap(self, store):
        for i in range(store.MAX_PENDING):
            assert _add(store, key=f"k{i}") is not None
        # One past the cap is dropped.
        assert _add(store, key="over") is None
        assert len(store.list_pending()) == store.MAX_PENDING

    def test_accept_creates_job_and_marks_accepted(self, store):
        rec = _add(store, key="acc", title="My Job")
        assert rec is not None
        created = {}

        def fake_create_job(**kwargs):
            created.update(kwargs)
            return {"id": "job123", "name": kwargs.get("name"), **kwargs}

        with patch("cron.jobs.create_job", fake_create_job):
            job = store.accept_suggestion("1", origin={"platform": "telegram", "chat_id": "5"})

        assert job is not None
        assert created["schedule"] == "0 9 * * *"
        assert created["origin"] == {"platform": "telegram", "chat_id": "5"}
        assert created["source_suggestion_id"] == rec["id"]
        # No longer pending.
        assert store.list_pending() == []
        # And accepting again is a no-op (not pending anymore).
        assert store.accept_suggestion("acc") is None

    def test_accept_from_desktop_captures_local_return_route(self, store):
        from gateway.session_context import clear_session_vars, set_session_vars

        _add(store, key="desktop-return", title="Desktop Job")
        created = {}

        def fake_create_job(**kwargs):
            created.update(kwargs)
            return {"id": "job-local", **kwargs}

        tokens = set_session_vars(source="desktop", session_id="desktop-suggestion-session")
        try:
            with patch("cron.jobs.create_job", fake_create_job):
                job = store.accept_suggestion("1")
        finally:
            clear_session_vars(tokens)

        assert job is not None
        assert created["local_session_origin"] == {
            "source": "desktop", "session_id": "desktop-suggestion-session"}

    def test_accept_uses_prevalidated_local_return_route(self, store):
        _add(store, key="validated-return", title="Desktop Job")
        created = {}

        def fake_create_job(**kwargs):
            created.update(kwargs)
            return {"id": "job-local", **kwargs}

        with patch("cron.jobs.create_job", fake_create_job):
            job = store.accept_suggestion(
                "1", local_session_origin={"source": "desktop", "session_id": "stored-chat"})

        assert job is not None
        assert created["local_session_origin"] == {"source": "desktop", "session_id": "stored-chat"}

    def test_explicit_external_delivery_does_not_gain_local_return(self, store):
        rec = store.add_suggestion(
            title="External", description="desc", source="catalog",
            job_spec={
                "prompt": "do it", "schedule": "0 9 * * *", "name": "External",
                "deliver": "telegram",
            },
            dedup_key="external-return",
        )
        assert rec is not None
        created = {}

        def fake_create_job(**kwargs):
            created.update(kwargs)
            return {"id": "job-external", **kwargs}

        with patch("cron.jobs.create_job", fake_create_job):
            job = store.accept_suggestion(
                rec["id"], local_session_origin={"source": "desktop", "session_id": "stored-chat"})

        assert job is not None
        assert "local_session_origin" not in created

    def test_concurrent_accept_creates_exactly_one_job(self, store):
        rec = _add(store, key="accept-race", title="Race Job")
        assert rec is not None
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def fake_create_job(**kwargs):
            calls.append(kwargs)
            entered.set()
            assert release.wait(5), "test did not release first create"
            return {"id": "race-job", **kwargs}

        with patch("cron.scheduler.create_job_with_scheduler_registration", fake_create_job):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(store.accept_suggestion, rec["id"])
                assert entered.wait(5), "first accept did not reach create"
                second = pool.submit(store.accept_suggestion, rec["id"])
                release.set()
                results = [first.result(timeout=5), second.result(timeout=5)]

        assert len(calls) == 1
        assert sum(result is not None for result in results) == 1
        assert store.get_suggestion(rec["id"])["status"] == "accepted"

    def test_registration_failure_marks_suggestion_accepted(self, store):
        """Retrying an acceptance must not create a duplicate durable job."""
        from cron.scheduler import CronSchedulerRegistrationError

        rec = _add(store, key="registration-failed", title="My Job")
        job = {"id": "job123", "name": "My Job"}
        failure = CronSchedulerRegistrationError(job, RuntimeError("private detail"))

        with patch(
            "cron.scheduler.create_job_with_scheduler_registration",
            side_effect=failure,
        ):
            with pytest.raises(CronSchedulerRegistrationError):
                store.accept_suggestion(rec["id"])

        assert store.list_pending() == []
        assert store.accept_suggestion(rec["id"]) is None

    def test_get_by_id_and_index_and_title(self, store):
        rec = _add(store, key="byref", title="Findable")
        assert store.get_suggestion(rec["id"])["id"] == rec["id"]
        assert store.get_suggestion("1")["id"] == rec["id"]
        assert store.get_suggestion("findable")["id"] == rec["id"]
        assert store.get_suggestion("nope") is None

    def test_clear_resolved_drops_accepted_only(self, store):
        _add(store, key="a")
        _add(store, key="b")
        store.dismiss_suggestion("2")  # b dismissed (retained for latch)
        with patch("cron.jobs.create_job", lambda **k: {"id": "j"}):
            store.accept_suggestion("1")  # a accepted
        removed = store.clear_resolved()
        assert removed == 1  # only the accepted record pruned
        # Dismissed record retained so its dedup_key still latches.
        assert _add(store, key="b") is None


class TestCatalog:
    def test_seed_registers_all_entries(self, store):
        from cron.suggestion_catalog import CATALOG, seed_catalog_suggestions

        created = seed_catalog_suggestions(add_fn=store.add_suggestion)
        assert len(created) == len(CATALOG)
        assert len(store.list_pending()) == min(len(CATALOG), store.MAX_PENDING)


    def test_monitor_entry_references_classifier_script(self):
        from cron.suggestion_catalog import CATALOG, classify_items_script_path

        monitor = next(e for e in CATALOG if e.key == "catalog:important-mail-monitor")
        # The prompt must reference the classifier by module path (resolvable
        # at run time on any backend), never by a baked-in absolute path —
        # absolute paths go stale after relocation and don't exist on remote
        # terminal backends (Docker/Modal).
        assert "cron.scripts.classify_items" in monitor.job_spec["prompt"]
        assert classify_items_script_path() not in monitor.job_spec["prompt"]
        assert Path(classify_items_script_path()).name == "classify_items.py"


class TestIntegrationSuggestions:
    def test_gmail_unlocks_mail_monitor_without_scheduling(self, store):
        from cron.suggestion_catalog import seed_integration_suggestions

        created = seed_integration_suggestions(["gmail"], add_fn=store.add_suggestion)

        assert [item["title"] for item in created] == ["Important-mail monitor"]
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0]["source"] == "integration"
        assert pending[0]["dedup_key"] == "catalog:important-mail-monitor"
        assert pending[0]["job_spec"]["schedule"] == "*/30 * * * *"
        assert pending[0]["job_spec"]["skills"] == ["email-inbox-triage"]
        assert "connectors__gmail__" in pending[0]["job_spec"]["prompt"]
        assert "connectors__outlook__" in pending[0]["job_spec"]["prompt"]
        assert "Do NOT run local Google Workspace OAuth setup" in pending[0]["job_spec"]["prompt"]
        assert pending[0]["job_spec"]["name"] == "Important-mail monitor"

    def test_google_calendar_unlocks_daily_briefing(self, store):
        from cron.suggestion_catalog import seed_integration_suggestions

        created = seed_integration_suggestions(["googlecalendar"], add_fn=store.add_suggestion)

        assert [item["title"] for item in created] == ["Daily briefing"]
        assert created[0]["source"] == "integration"
        assert created[0]["dedup_key"] == "catalog:daily-briefing"
        assert created[0]["job_spec"].get("skills") in (None, [])
        assert "connectors__googlecalendar__" in created[0]["job_spec"]["prompt"]
        assert "Do NOT run local Google Workspace OAuth" in created[0]["job_spec"]["prompt"]
        assert created[0]["job_spec"]["schedule"] == "0 8 * * *"
        assert created[0]["job_spec"]["name"] == "Daily briefing"

    def test_managed_calendar_suggestion_never_requires_local_google_credentials(self, store):
        from cron.suggestion_catalog import seed_integration_suggestions

        created = seed_integration_suggestions(["googlecalendar"], add_fn=store.add_suggestion)

        spec = created[0]["job_spec"]
        assert "google-workspace" not in (spec.get("skills") or [])
        assert "gws" in spec["prompt"]
        assert "second Google credential" in spec["prompt"]

    def test_mail_connectors_share_one_dedup_decision(self, store):
        from cron.suggestion_catalog import seed_integration_suggestions

        created = seed_integration_suggestions(["gmail", "outlook"], add_fn=store.add_suggestion)

        assert len(created) == 1
        assert len(store.list_pending()) == 1

    def test_prior_catalog_dismissal_is_not_reoffered_on_connect(self, store):
        from cron.suggestion_catalog import seed_catalog_suggestions, seed_integration_suggestions

        seeded = seed_catalog_suggestions(
            add_fn=store.add_suggestion, keys=["catalog:important-mail-monitor"])
        assert len(seeded) == 1
        assert store.dismiss_suggestion(seeded[0]["id"]) is True

        assert seed_integration_suggestions(["gmail"], add_fn=store.add_suggestion) == []
        assert store.list_pending() == []

    def test_unrelated_connector_does_not_invent_automation(self, store):
        from cron.suggestion_catalog import seed_integration_suggestions

        assert seed_integration_suggestions(["notion"], add_fn=store.add_suggestion) == []
        assert store.list_pending() == []


class TestBlueprintBridge:
    def test_blueprint_registers_suggestion(self, store):
        from tools.blueprints import BlueprintSpec, register_blueprint_suggestion

        spec = BlueprintSpec(skill_name="morning-brief", schedule="0 8 * * *", deliver="telegram")
        with patch("cron.suggestions.add_suggestion", store.add_suggestion):
            rec = register_blueprint_suggestion(spec)
        assert rec is not None
        assert rec["source"] == "blueprint"
        assert rec["job_spec"]["skills"] == ["morning-brief"]
        assert rec["job_spec"]["schedule"] == "0 8 * * *"


class TestCommandHandler:
    def test_bare_lists_pending(self, store):
        _add(store, key="c1", title="Daily thing")
        with patch("cron.suggestions.list_pending", store.list_pending):
            from hermes_cli.suggestions_cmd import handle_suggestions_command
            # Patch the module the handler imports.
            with patch.dict("sys.modules"):
                out = handle_suggestions_command("")
        assert "Daily thing" in out


    def test_empty_list_message(self, store):
        from hermes_cli.suggestions_cmd import handle_suggestions_command

        out = handle_suggestions_command("")
        assert "No suggested automations" in out

    def test_aux_monitor_config_default(self):
        from hermes_cli.config import DEFAULT_CONFIG

        assert "monitor" in DEFAULT_CONFIG["auxiliary"]
        assert DEFAULT_CONFIG["auxiliary"]["monitor"]["provider"] == "auto"
