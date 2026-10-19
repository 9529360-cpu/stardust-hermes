"""Durability policy for pending deterministic reminders."""

import queue
import time


def _publish(ad, delegation_id, *, role="reminder", session="reminder-session"):
    return ad.publish_durable_completion(
        delegation_id=delegation_id,
        session_key=session,
        parent_session_id=session,
        goal="Reminder",
        summary="Do the thing.",
        role=role,
        event_metadata={
            "cron_job_id": "job-1",
            "cron_job_name": "Reminder",
            "reminder_due_at": "2026-10-20T09:00:00+08:00",
        } if role == "reminder" else {},
    )


def _age_row(ad, delegation_id, *, seconds):
    old = time.time() - seconds
    with ad._DB_LOCK, ad._transaction() as conn:
        conn.execute(
            """UPDATE async_delegations
               SET dispatched_at=?, completed_at=?, updated_at=?
               WHERE delegation_id=?""",
            (old, old, old, delegation_id),
        )


def test_old_pending_reminder_replays_beyond_generic_48h_cap(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "test-worker")
    assert _publish(ad, "reminder-old") is True
    _age_row(ad, "reminder-old", seconds=5 * 24 * 3600)

    q = queue.Queue()
    restored = ad.restore_matching_undelivered_completions(
        q, lambda evt: evt.get("session_key") == "reminder-session")

    assert restored == 1
    evt = q.get_nowait()
    assert evt["delegation_id"] == "reminder-old"
    assert evt["role"] == "reminder"
    assert evt["restored"] is True
    assert ad.get_durable_delegation("reminder-old")["delivery_state"] == "pending"


def test_old_ordinary_completion_still_drops_at_generic_replay_cap(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "test-worker")
    assert _publish(ad, "ordinary-old", role="cron_run") is True
    _age_row(ad, "ordinary-old", seconds=5 * 24 * 3600)

    q = queue.Queue()
    restored = ad.restore_matching_undelivered_completions(
        q, lambda evt: evt.get("session_key") == "reminder-session")

    assert restored == 0
    assert q.empty()
    assert ad.get_durable_delegation("ordinary-old")["delivery_state"] == "dropped"


def test_pending_reminders_do_not_count_against_completed_history_cap(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "test-worker")
    for index in range(ad._MAX_RETAINED_COMPLETED + 12):
        assert _publish(ad, f"reminder-many-{index}") is True

    with ad._DB_LOCK, ad._transaction() as conn:
        count = conn.execute(
            """SELECT COUNT(*) FROM async_delegations
               WHERE delivery_state='pending'""").fetchone()[0]

    assert count == ad._MAX_RETAINED_COMPLETED + 12
    assert ad.get_durable_delegation("reminder-many-0")["delivery_state"] == "pending"


def test_pending_reminder_does_not_drop_after_delivery_attempt_budget(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "test-worker")
    assert _publish(ad, "reminder-retry") is True

    for index in range(ad._MAX_DELIVERY_ATTEMPTS + 3):
        claim = f"consumer-{index}"
        assert ad.claim_completion_delivery("reminder-retry", claim) is True
        assert ad.release_completion_delivery("reminder-retry", claim) is True

    receipt = ad.get_durable_delegation("reminder-retry")
    assert receipt["delivery_state"] == "pending"
    assert receipt["delivery_attempts"] == ad._MAX_DELIVERY_ATTEMPTS + 3


def test_delivered_reminder_returns_to_normal_retention_policy(tmp_path, monkeypatch):
    from tools import async_delegation as ad

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "test-worker")
    assert _publish(ad, "reminder-delivered") is True

    claim = "consumer"
    assert ad.claim_completion_delivery("reminder-delivered", claim) is True
    assert ad.complete_completion_delivery("reminder-delivered", claim) is True
    _age_row(
        ad,
        "reminder-delivered",
        seconds=ad._DURABLE_RETENTION_SECONDS + 3600,
    )

    ad._prune_durable_records()

    assert ad.get_durable_delegation("reminder-delivered") is None
