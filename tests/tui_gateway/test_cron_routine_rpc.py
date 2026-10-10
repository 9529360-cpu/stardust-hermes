"""Gap 7: a routine created from chat is listed, paused, resumed and removed over cron.manage.

The desktop sends the job id as ``name`` for pause, resume and remove and reads the job id back
as ``job_id`` in list replies. These tests drive the same RPC seam.
"""

import json

import pytest

from tui_gateway import server


@pytest.fixture(autouse=True)
def _isolated_cron_store(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")


def _manage(params):
    resp = server.handle_request({"id": "1", "method": "cron.manage", "params": params})
    assert "result" in resp, resp
    return resp["result"]


def _create_routine():
    from tools.cronjob_tools import cronjob

    return json.loads(
        cronjob(action="create", prompt="Summarize my inbox", schedule="every weekday 8:00")
    )["job_id"]


def test_routine_is_listed_paused_resumed_and_removed_over_rpc():
    job_id = _create_routine()

    listed = _manage({"action": "list", "include_disabled": True})
    assert [job["job_id"] for job in listed["jobs"]] == [job_id]
    assert listed["jobs"][0]["enabled"] is True

    paused = _manage({"action": "pause", "name": job_id})
    assert paused["success"] is True
    assert paused["job"]["enabled"] is False
    assert _manage({"action": "list"})["jobs"] == []
    assert _manage({"action": "list", "include_disabled": True})["jobs"][0]["enabled"] is False

    resumed = _manage({"action": "resume", "name": job_id})
    assert resumed["success"] is True
    assert resumed["job"]["enabled"] is True
    assert _manage({"action": "list"})["jobs"][0]["job_id"] == job_id

    removed = _manage({"action": "remove", "name": job_id})
    assert removed["success"] is True
    assert removed["removed_job"]["id"] == job_id
    assert _manage({"action": "list", "include_disabled": True})["jobs"] == []


def test_pausing_an_unknown_routine_is_reported_as_a_failed_result():
    result = _manage({"action": "pause", "name": "no-such-routine"})

    assert result["success"] is False
    assert "no-such-routine" in result["error"]


def test_a_routine_run_appears_in_the_work_ledger_read(monkeypatch, tmp_path):
    from cron import executions

    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "cron" / "executions.db")
    job_id = _create_routine()
    run = executions.create_execution(job_id, source="scheduler")

    resp = server.handle_request({"id": "2", "method": "cron.executions.list", "params": {"limit": 20}})

    assert "result" in resp, resp
    work = resp["result"]["work"]
    assert [item["id"] for item in work] == [f"cron:{run['id']}"]
    assert work[0]["kind"] == "cron"
    assert work[0]["status"] == "running"
    assert work[0]["title"] == f"Cron job {job_id}"
