"""Durable deterministic reminder contracts."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def tmp_cron_store(tmp_path, monkeypatch):
    import cron.jobs as jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")
    return jobs


def _origin():
    return {"source": "desktop", "session_id": "chat-1"}


def test_reminder_job_is_one_shot_local_and_deterministic(tmp_cron_store):
    jobs = tmp_cron_store
    job = jobs.create_job(
        prompt="明天九点去办护照。",
        schedule="2099-01-01T09:00:00+00:00",
        local_session_origin=_origin(),
        reminder=True,
    )

    assert job["reminder"] is True
    assert job["repeat"]["times"] == 1
    assert job["local_session_origin"] == _origin()
    assert job["provider_snapshot"] is None
    assert job["model_snapshot"] is None


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"schedule": "every 1h"}, "one-shot"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "repeat": 2}, "exactly one"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "local_session_origin": None}, "Desktop/TUI"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "script": "collect.py"}, "deterministic"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "skills": ["research"]}, "deterministic"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "attach_to_session": True}, "deterministic"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "enabled_toolsets": ["web"]}, "deterministic"),
        ({"schedule": "2099-01-01T09:00:00+00:00", "deliver": "slack:alerts"}, "local/origin"),
    ],
)
def test_reminder_job_rejects_ambiguous_execution_modes(tmp_cron_store, kwargs, message):
    jobs = tmp_cron_store
    params = {
        "prompt": "Reminder text",
        "local_session_origin": _origin(),
        "reminder": True,
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        jobs.create_job(**params)


def test_reminder_delivery_identity_is_stable_per_occurrence():
    from cron.scheduler import _durable_reminder_identity

    base = {
        "id": "reminder-1",
        "schedule": {"kind": "once", "run_at": "2026-10-20T09:00:00+00:00"},
        "_scheduled_instant": "2026-10-20T09:00:00+00:00",
    }
    first = _durable_reminder_identity({**base, "execution_id": "exec-a"})
    second = _durable_reminder_identity({**base, "execution_id": "exec-b"})
    later = _durable_reminder_identity({
        **base,
        "_scheduled_instant": "2026-10-21T09:00:00+00:00",
    })

    assert first == second
    assert first[0].startswith("cron_reminder_")
    assert later[0] != first[0]


def test_publish_reminder_uses_stable_durable_completion(monkeypatch):
    import cron.scheduler as scheduler
    import tools.async_delegation as async_delegation

    calls = []

    @contextmanager
    def side_effect_fence():
        yield True

    class Fence:
        pass

    Fence.side_effect_fence = staticmethod(side_effect_fence)

    monkeypatch.setattr(
        async_delegation,
        "publish_durable_completion",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    job = {
        "id": "reminder-1",
        "name": "Passport",
        "prompt": "去办护照。",
        "local_session_origin": _origin(),
        "schedule": {"kind": "once", "run_at": "2026-10-20T09:00:00+00:00"},
        "_scheduled_instant": "2026-10-20T09:00:00+00:00",
    }

    first = scheduler._publish_durable_reminder(job, Fence())
    second = scheduler._publish_durable_reminder({**job, "execution_id": "other"}, Fence())

    assert first == second
    assert [call["delegation_id"] for call in calls] == [first, first]
    assert calls[0]["role"] == "reminder"
    assert calls[0]["summary"] == "去办护照。"
    assert calls[0]["session_key"] == "chat-1"


def test_run_body_publishes_reminder_before_consuming_dispatch(monkeypatch):
    import cron.scheduler as scheduler

    order = []
    monkeypatch.delenv("_HERMES_CRON_EXTERNAL_WORKER", raising=False)
    monkeypatch.setattr(scheduler, "mark_execution_running", lambda _eid: {"status": "running"})
    monkeypatch.setattr(
        scheduler, "_publish_durable_reminder",
        lambda _job, _fence: order.append("publish") or "cron_reminder_key",
    )
    monkeypatch.setattr(
        scheduler, "claim_dispatch", lambda _jid: order.append("claim_dispatch") or True)
    monkeypatch.setattr(
        scheduler, "mark_job_run",
        lambda *_args, **_kwargs: order.append("mark_job_run") or True)
    monkeypatch.setattr(
        scheduler, "_settle_durable_reminder_receipt",
        lambda *_args, **_kwargs: order.append("settle"))
    monkeypatch.setattr(
        scheduler, "finish_execution",
        lambda *_args, **_kwargs: order.append("finish_execution") or {})
    job = {
        "id": "reminder-1",
        "name": "Passport",
        "prompt": "去办护照。",
        "reminder": True,
        "execution_id": "exec-1",
        "schedule": {"kind": "once", "run_at": "2026-10-20T09:00:00+00:00"},
        "repeat": {"times": 1, "completed": 0},
        "local_session_origin": _origin(),
    }

    assert scheduler._run_one_job_body(job) is True
    assert order[:4] == ["publish", "claim_dispatch", "mark_job_run", "settle"]
    assert order[-1] == "finish_execution"


def test_pre_persistence_failure_does_not_consume_dispatch(monkeypatch):
    import cron.jobs_runs as jobs_runs
    import cron.scheduler as scheduler

    released = []
    monkeypatch.delenv("_HERMES_CRON_EXTERNAL_WORKER", raising=False)
    monkeypatch.setattr(scheduler, "mark_execution_running", lambda _eid: {"status": "running"})
    monkeypatch.setattr(
        scheduler, "_publish_durable_reminder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk unavailable")),
    )
    monkeypatch.setattr(
        scheduler, "claim_dispatch",
        lambda _jid: pytest.fail("dispatch budget must not be consumed"))
    monkeypatch.setattr(
        scheduler, "mark_job_run",
        lambda *_args, **_kwargs: pytest.fail("failed persistence must not mark the occurrence run"))
    monkeypatch.setattr(
        jobs_runs, "release_pre_dispatch_claims",
        lambda jid, **kwargs: released.append((jid, kwargs)) or True,
    )
    finished = []
    monkeypatch.setattr(
        scheduler, "finish_execution",
        lambda *args, **kwargs: finished.append((args, kwargs)) or {})
    job = {
        "id": "reminder-1",
        "name": "Passport",
        "prompt": "去办护照。",
        "reminder": True,
        "execution_id": "exec-1",
        "schedule": {"kind": "once", "run_at": "2026-10-20T09:00:00+00:00"},
        "repeat": {"times": 1, "completed": 0},
        "local_session_origin": _origin(),
    }

    assert scheduler._run_one_job_body(job) is False
    assert released and released[0][0] == "reminder-1"
    assert finished[-1][1]["success"] is False


def test_overdue_unconsumed_reminder_replays_past_generic_grace(monkeypatch):
    from cron import jobs_due

    now = datetime.now(timezone.utc)
    run_dt = now - timedelta(days=2)
    run_at = run_dt.isoformat()
    stale_at = (now - timedelta(hours=1)).isoformat()
    job = {
        "id": "reminder-1",
        "name": "Passport",
        "reminder": True,
        "schedule": {"kind": "once", "run_at": run_at},
        "repeat": {"times": 1, "completed": 0},
        "run_claim": {"at": stale_at, "by": "foreign:999999"},
        "fire_claim": {"at": stale_at, "by": "foreign:999999:token"},
    }
    raw = dict(job)
    scan = jobs_due._DueScan([raw], now)
    due = jobs_due._DueJob(job, scan, run_at, run_dt, run_dt)

    assert jobs_due._retire_expired_oneshot(due) is False
    assert job["run_claim"] is None
    assert job["fire_claim"] is None
    assert raw["run_claim"] is None
    assert raw["fire_claim"] is None


def test_overdue_consumed_reminder_flows_to_dispatch_limit_guard():
    from cron import jobs_due

    now = datetime.now(timezone.utc)
    run_dt = now - timedelta(days=2)
    run_at = run_dt.isoformat()
    job = {
        "id": "reminder-1",
        "name": "Passport",
        "reminder": True,
        "schedule": {"kind": "once", "run_at": run_at},
        "repeat": {"times": 1, "completed": 1},
    }
    scan = jobs_due._DueScan([dict(job)], now)
    due = jobs_due._DueJob(job, scan, run_at, run_dt, run_dt)

    assert jobs_due._retire_expired_oneshot(due) is False


def test_reminder_rejects_persistent_memory_scope(tmp_cron_store):
    jobs = tmp_cron_store

    with pytest.raises(ValueError, match="deterministic"):
        jobs.create_job(
            prompt="Private reminder.",
            schedule="2099-01-01T09:00:00+00:00",
            local_session_origin=_origin(),
            reminder=True,
            memory_scope="none",
        )


def test_reminder_execution_mode_cannot_be_toggled_by_update(tmp_cron_store):
    jobs = tmp_cron_store
    reminder = jobs.create_job(
        prompt="Reminder.",
        schedule="2099-01-01T09:00:00+00:00",
        local_session_origin=_origin(),
        reminder=True,
    )
    ordinary = jobs.create_job(
        prompt="Ordinary task.",
        schedule="2099-01-01T10:00:00+00:00",
    )

    with pytest.raises(ValueError, match="fixed at creation"):
        jobs.update_job(reminder["id"], {"reminder": False})
    with pytest.raises(ValueError, match="fixed at creation"):
        jobs.update_job(ordinary["id"], {"reminder": True})
