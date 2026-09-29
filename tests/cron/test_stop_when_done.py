"""Regression coverage for goal-oriented cron jobs that stop when done."""

import pytest


@pytest.fixture()
def tmp_cron_store(tmp_path, monkeypatch):
    import cron.jobs as jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")
    return jobs


def test_done_marker_only_matches_standalone_edge_line():
    from cron.scheduler import _extract_cron_done_response

    cleaned, done = _extract_cron_done_response("[DONE]\nPackage delivered at 15:04.")
    assert done is True
    assert cleaned == "Package delivered at 15:04."

    cleaned, done = _extract_cron_done_response("Package delivered.\n[DONE]")
    assert done is True
    assert cleaned == "Package delivered."

    cleaned, done = _extract_cron_done_response("The docs mention [DONE] as an example.")
    assert done is False
    assert cleaned == "The docs mention [DONE] as an example."


def test_marker_only_still_has_user_facing_completion_copy():
    from cron.scheduler import _extract_cron_done_response

    assert _extract_cron_done_response("[DONE]") == ("Task completed.", True)


def test_done_plus_silent_still_notifies_completion():
    from cron.scheduler import _extract_cron_done_response

    assert _extract_cron_done_response("[DONE]\n[SILENT]") == ("Task completed.", True)


def test_goal_hint_is_opt_in():
    from cron.scheduler_prompt import _build_job_prompt

    ordinary = _build_job_prompt({
        "id": "ordinary",
        "prompt": "Check package status.",
    })
    goal = _build_job_prompt({
        "id": "goal",
        "prompt": "Check package status.",
        "stop_when_done": True,
    })

    assert "GOAL COMPLETION" not in ordinary
    assert "literal ASCII token \"[DONE]\"" not in ordinary
    assert "GOAL COMPLETION" in goal
    assert "literal ASCII token \"[DONE]\"" in goal


def test_terminal_completion_reuses_existing_completed_state(tmp_cron_store):
    jobs = tmp_cron_store

    job = jobs.create_job(
        prompt="Track the package until delivered.",
        schedule="every 1h",
        stop_when_done=True,
    )
    assert job["stop_when_done"] is True
    assert job["state"] == "scheduled"

    assert jobs.mark_job_run(job["id"], True, terminal_complete=True) is True
    stored = jobs.get_job(job["id"])

    assert stored["state"] == "completed"
    assert stored["enabled"] is False
    assert stored["next_run_at"] is None
    assert stored["last_status"] == "ok"


def test_stop_when_done_can_be_disabled_without_leaving_stale_field(tmp_cron_store):
    jobs = tmp_cron_store

    job = jobs.create_job(
        prompt="Track the refund until received.",
        schedule="every 2h",
        stop_when_done=True,
    )
    updated = jobs.update_job(job["id"], {"stop_when_done": False})

    assert updated is not None
    assert "stop_when_done" not in updated


def test_stop_when_done_rejects_no_agent_mode(tmp_cron_store):
    jobs = tmp_cron_store

    with pytest.raises(ValueError, match="stop_when_done requires an agent run"):
        jobs.create_job(
            prompt="",
            schedule="every 1h",
            no_agent=True,
            script="poll.py",
            stop_when_done=True,
        )


def test_finish_completed_run_forwards_terminal_completion(monkeypatch):
    import cron.scheduler as scheduler

    marked = []
    finished = []

    monkeypatch.setattr(
        scheduler,
        "mark_job_run",
        lambda *args, **kwargs: marked.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "finish_execution",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )

    delivery = scheduler._RunDelivery(
        job={"id": "goal-1", "deliver": "local"},
        success=True,
        error=None,
        should_deliver=True,
        delivery_content="Package delivered.",
        terminal_complete=True,
    )

    assert scheduler._finish_completed_run(delivery, None, "exec-goal-1") is True
    assert marked == [
        (
            ("goal-1", True, None),
            {"delivery_error": None, "terminal_complete": True},
        )
    ]
    assert finished == [
        (
            ("exec-goal-1",),
            {"success": True, "error": None, "delivery_outcome": "suppressed"},
        )
    ]


def test_delivery_failure_does_not_reschedule_a_completed_real_world_goal(tmp_cron_store):
    jobs = tmp_cron_store

    job = jobs.create_job(
        prompt="Track the repair until resolved.",
        schedule="every 1h",
        stop_when_done=True,
    )
    assert jobs.mark_job_run(
        job["id"],
        True,
        delivery_error="notification transport unavailable",
        terminal_complete=True,
    ) is True

    stored = jobs.get_job(job["id"])
    assert stored["state"] == "completed"
    assert stored["next_run_at"] is None
    assert stored["last_status"] == "delivery_failed"



def test_run_body_strips_done_before_delivery_and_marks_terminal(monkeypatch):
    import agent.secret_scope as secret_scope
    import cron.scheduler as scheduler
    import tools.terminal_scope as terminal_scope

    observed = {}

    monkeypatch.setattr(scheduler, "claim_dispatch", lambda _job_id: True)
    monkeypatch.setattr(scheduler, "mark_execution_running", lambda _execution_id: {})
    monkeypatch.setattr(
        scheduler,
        "run_job",
        lambda *_args, **_kwargs: (
            True,
            "raw audit output",
            "[DONE]\nPackage delivered at the front desk.",
            None,
        ),
    )
    monkeypatch.setattr(scheduler, "_consume_interrupted_flag", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        secret_scope,
        "build_profile_secret_scope",
        lambda _home: object(),
    )
    monkeypatch.setattr(secret_scope, "set_secret_scope", lambda _scope: "secret-token")
    monkeypatch.setattr(
        secret_scope,
        "reset_secret_scope",
        lambda token: observed.setdefault("secret_reset", token),
    )
    monkeypatch.setattr(
        terminal_scope,
        "install_profile_terminal_scope",
        lambda _home: "terminal-token",
    )
    monkeypatch.setattr(
        terminal_scope,
        "reset_terminal_scope",
        lambda token: observed.setdefault("terminal_reset", token),
    )

    def fake_save_compose(delivery, _fence, final_response, _output, **_kwargs):
        observed["final_response"] = final_response
        delivery.should_deliver = True
        delivery.delivery_content = final_response

    monkeypatch.setattr(scheduler, "_save_compose_deliver", fake_save_compose)
    monkeypatch.setattr(
        scheduler,
        "_publish_local_session_completion",
        lambda *_args, **_kwargs: None,
    )

    def fake_finish(delivery, _owner, execution_id):
        observed["terminal_complete"] = delivery.terminal_complete
        observed["execution_id"] = execution_id
        return True

    monkeypatch.setattr(scheduler, "_finish_completed_run", fake_finish)

    job = {
        "id": "goal-run",
        "name": "Track package",
        "execution_id": "exec-goal-run",
        "stop_when_done": True,
    }
    assert scheduler._run_one_job_body(job) is True

    assert observed["final_response"] == "Package delivered at the front desk."
    assert observed["terminal_complete"] is True
    assert observed["execution_id"] == "exec-goal-run"
    assert observed["secret_reset"] == "secret-token"
    assert observed["terminal_reset"] == "terminal-token"
