"""Event triggers through the scheduler tick: startup once per start, folder runs, the source recorded
in the executions ledger, the prompt the agent receives, retry, and the two requeue paths.

The agent run is replaced by a stub that closes the execution and settles the job the way the real
run does. Everything else (tick, claims, submission, the ledger and the store) is the real code.
"""

import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    from cron import jobs_triggers as jt
    from cron import scheduler
    from cron import triggers as T

    clock = {"now": 2_000_000.0}
    monkeypatch.setattr(T, "now_epoch", lambda: clock["now"])
    monkeypatch.setattr(jt, "_ticker_started", True)
    monkeypatch.setattr(jt, "_armed_stores", set())

    state = SimpleNamespace(clock=clock, runs=[], outcomes=[])

    def fake_run_one_job(job, *, adapters=None, loop=None, verbose=False, **_ignored):
        from cron.executions import finish_execution
        from cron.jobs import mark_job_run

        ok = state.outcomes.pop(0) if state.outcomes else True
        state.runs.append({"job": job, "event": job.get("trigger_event")})
        finish_execution(job["execution_id"], success=ok, error=None if ok else "boom")
        mark_job_run(job["id"], ok, None if ok else "boom")
        return True

    monkeypatch.setattr(scheduler, "run_one_job", fake_run_one_job)
    state.tick = lambda: scheduler.tick(verbose=False, sync=True)
    return state


def _startup(name="brief"):
    from cron.jobs import create_job

    return create_job(prompt="Summarise today", schedule=None, name=name, trigger={"type": "startup"})


def _stored(job_id):
    from cron.jobs import load_jobs

    return next(item for item in load_jobs() if item["id"] == job_id)


class TestStartup:
    def test_a_startup_job_runs_once_per_ticker_start(self, ticker):
        from cron import jobs_triggers as jt

        _startup()
        assert ticker.tick() == 1
        assert ticker.tick() == 0
        assert [run["event"]["kind"] for run in ticker.runs] == ["startup"]
        jt._armed_stores.clear()  # a new backend start, in this test process
        assert ticker.tick() == 1
        assert len(ticker.runs) == 2

    def test_a_startup_job_created_after_the_ticker_started_waits_for_the_next_start(self, ticker):
        from cron import jobs_triggers as jt

        assert ticker.tick() == 0  # first tick of this start, with nothing configured yet
        _startup()  # created later in the same process: not a start, so no run now
        assert ticker.tick() == 0
        assert ticker.runs == []
        jt._armed_stores.clear()  # the next backend start
        assert ticker.tick() == 1

    def test_a_store_with_no_event_jobs_is_never_written_by_the_trigger_pass(self, ticker, tmp_path):
        assert ticker.tick() == 0
        assert ticker.tick() == 0
        assert not (tmp_path / "home" / "cron" / "jobs.json").exists()

    def test_nothing_runs_before_the_built_in_ticker_has_started(self, ticker, monkeypatch):
        from cron import jobs_triggers as jt

        monkeypatch.setattr(jt, "_ticker_started", False)
        _startup()
        assert ticker.tick() == 0
        assert ticker.runs == []

    def test_the_startup_run_is_recorded_with_source_startup(self, ticker):
        from cron.executions import list_executions

        job = _startup()
        ticker.tick()
        rows = list_executions(job_id=job["id"])
        assert [row["source"] for row in rows] == ["startup"]
        assert rows[0]["status"] == "completed"

    def test_a_startup_prompt_says_no_file_is_attached(self, ticker):
        from cron.scheduler import _build_job_prompt

        _startup()
        ticker.tick()
        prompt = _build_job_prompt(ticker.runs[0]["job"])
        assert "## Trigger" in prompt
        assert "No file is attached" in prompt


class TestFolder:
    def test_a_new_file_runs_once_and_the_prompt_has_metadata_only(self, ticker, tmp_path):
        from cron.executions import list_executions
        from cron.jobs import create_job
        from cron.scheduler import _build_job_prompt

        inbox = tmp_path / "inbox"
        inbox.mkdir()
        job = create_job(prompt="Sort the new file", schedule=None, name="sorter",
                         trigger={"type": "folder", "watch_dir": str(inbox)})
        base = ticker.clock["now"]
        content = b"ACCOUNT-NUMBER-0000"
        statement = inbox / "statement.pdf"
        statement.write_bytes(content)
        os.utime(statement, (base + 5, base + 5))

        assert ticker.tick() == 0  # first sight
        ticker.clock["now"] = base + 60
        assert ticker.tick() == 0  # unchanged once
        ticker.clock["now"] = base + 120
        assert ticker.tick() == 1  # settled: the one run
        ticker.clock["now"] = base + 180
        assert ticker.tick() == 0  # never again

        assert len(ticker.runs) == 1
        run = ticker.runs[0]
        assert run["event"]["file"]["name"] == "statement.pdf"
        prompt = _build_job_prompt(run["job"])
        assert '"name": "statement.pdf"' in prompt
        assert f'"size_bytes": {len(content)}' in prompt
        assert "ACCOUNT-NUMBER" not in prompt  # the contents are never read or sent
        assert [row["source"] for row in list_executions(job_id=job["id"])] == ["folder"]

    def test_a_schedule_job_run_now_still_dispatches_as_before(self, ticker):
        from cron.executions import list_executions
        from cron.jobs import create_job, trigger_job

        job = create_job(prompt="Check mail", schedule="every 2h", name="mail")
        trigger_job(job["id"])
        assert ticker.tick() == 1
        assert ticker.runs[0]["event"] is None
        assert [row["source"] for row in list_executions(job_id=job["id"])] == ["builtin"]


class TestRetryAndRequeue:
    def test_a_failing_run_is_retried_once_and_the_error_is_recorded(self, ticker):
        from cron.jobs import get_job

        job = _startup()
        ticker.outcomes.extend([False, False])
        assert ticker.tick() == 1
        assert ticker.tick() == 1  # the single retry
        assert ticker.tick() == 0  # no third attempt
        assert [run["event"]["attempt"] for run in ticker.runs] == [1, 2]
        stored = get_job(job["id"])
        assert stored["last_status"] == "error"
        assert stored["last_error"] == "boom"

    def test_a_claim_held_elsewhere_keeps_the_event_for_a_later_tick(self, ticker, monkeypatch):
        from cron import scheduler

        _startup()
        original_claim = scheduler.claim_job_for_fire
        monkeypatch.setattr(scheduler, "claim_job_for_fire", lambda *args, **kwargs: False)
        assert ticker.tick() == 1  # the attempt is counted, but nothing ran
        assert ticker.runs == []
        monkeypatch.setattr(scheduler, "claim_job_for_fire", original_claim)
        assert ticker.tick() == 1
        assert [run["event"]["kind"] for run in ticker.runs] == ["startup"]

    def test_a_submission_that_is_refused_keeps_the_event_for_a_later_tick(self, ticker, monkeypatch):
        from cron import scheduler

        _startup()
        original_submit = scheduler._submit_with_guard
        monkeypatch.setattr(scheduler, "_submit_with_guard", lambda *args, **kwargs: None)
        assert ticker.tick() == 0
        assert ticker.runs == []
        monkeypatch.setattr(scheduler, "_submit_with_guard", original_submit)
        assert ticker.tick() == 1
        assert len(ticker.runs) == 1
        assert _stored(_startup_id(ticker))["trigger_state"]["queue"] == []


def _startup_id(ticker):
    from cron.jobs import load_jobs

    return next(item["id"] for item in load_jobs() if item.get("trigger", {}).get("type") == "startup")
