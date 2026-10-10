"""Gap 7: a routine said in chat becomes a cron job that delivers back to that chat.

The model turns "every weekday 8:00 summarize my inbox" into the schedule grammar and calls the
cronjob tool from a chat session. These tests pin the mapping the model relies on: the phrase
becomes a weekday cron expression, and omitted delivery resolves to the originating chat.
"""

import json

import pytest

from tools.cronjob_tools import cronjob


@pytest.fixture(autouse=True)
def _chat_origin_and_isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    from gateway.session_context import clear_session_vars, set_session_vars

    tokens = set_session_vars(platform="telegram", chat_id="999")
    yield
    clear_session_vars(tokens)


def test_weekday_phrase_from_chat_becomes_a_weekday_cron_job_delivering_to_that_chat():
    created = json.loads(
        cronjob(action="create", prompt="Summarize my inbox", schedule="every weekday 8:00")
    )

    assert created["success"] is True
    assert created["deliver"] == "origin"

    from cron.jobs import get_job

    job = get_job(created["job_id"])
    assert job["schedule"]["expr"] == "0 8 * * 1-5"
    assert job["origin"]["platform"] == "telegram"
    assert job["origin"]["chat_id"] == "999"
    assert job["next_run_at"]


def test_daily_time_phrase_from_chat_becomes_a_daily_cron_job():
    created = json.loads(
        cronjob(action="create", prompt="Summarize my inbox", schedule="every day at 8am")
    )

    assert created["success"] is True

    from cron.jobs import get_job

    assert get_job(created["job_id"])["schedule"]["expr"] == "0 8 * * *"
