"""Regression coverage for job-scoped cron approval delegation."""

import contextvars

import pytest


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    """Keep cron persistence isolated from the operator profile."""
    import cron.jobs as jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")
    return cron_dir


def test_job_scoped_approval_mode_roundtrips_and_inherit_clears(tmp_cron_dir):
    from cron.jobs import create_job, get_job, load_jobs, update_job

    job = create_job(
        prompt="Continue the maintenance task.",
        schedule="every 1h",
        approval_mode="approve",
    )
    assert get_job(job["id"])["approval_mode"] == "approve"

    updated = update_job(job["id"], {"approval_mode": "inherit"})
    assert updated is not None
    assert updated.get("approval_mode") is None
    stored = next(item for item in load_jobs() if item["id"] == job["id"])
    assert "approval_mode" not in stored


def test_invalid_job_scoped_approval_mode_is_rejected(tmp_cron_dir):
    from cron.jobs import create_job

    with pytest.raises(ValueError, match="Invalid approval_mode"):
        create_job(
            prompt="Continue the maintenance task.",
            schedule="every 1h",
            approval_mode="unrestricted",
        )


def _isolate_approval_gate(monkeypatch):
    import tools.approval as approval
    from tools import approval_context

    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False, raising=False)
    monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval, "is_approved", lambda *_a, **_kw: False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(approval_context, "_binary_approval_mode", lambda _key: "deny")


def test_approved_parent_cron_keeps_own_authority_but_cannot_delegate_it(monkeypatch):
    from cron.scheduler import _CronRunScope
    from tools import approval_context
    from tools.cronjob_tools import _approval_mode_change_error

    _isolate_approval_gate(monkeypatch)
    scope = _CronRunScope({"approval_mode": "approve"}, "parent", None)
    try:
        scope.enter()
        assert approval_context._get_cron_approval_mode() == "approve"
        copied = contextvars.copy_context()
        assert copied.run(approval_context._get_cron_approval_mode) == "approve"
        error = _approval_mode_change_error(
            current_mode=None, requested_mode="approve", job_label="child"
        )
    finally:
        scope.exit()

    assert error is not None
    assert "live human confirmation" in error.lower()
    assert approval_context._get_cron_approval_mode() == "deny"

def test_denied_parent_cron_cannot_bootstrap_itself_to_approve(monkeypatch):
    from cron.scheduler import _CronRunScope
    from tools.cronjob_tools import _approval_mode_change_error

    _isolate_approval_gate(monkeypatch)
    scope = _CronRunScope({"approval_mode": "deny"}, "parent", None)
    try:
        scope.enter()
        error = _approval_mode_change_error(
            current_mode=None, requested_mode="approve", job_label="child"
        )
    finally:
        scope.exit()

    assert error is not None
    assert "cron" in error.lower()


def test_yolo_session_cannot_silently_become_durable_cron_approval(monkeypatch):
    import tools.approval as approval
    from tools import approval_context
    from tools.cronjob_tools import _approval_mode_change_error

    monkeypatch.setattr(approval, "_yolo_active", lambda: True)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    error = _approval_mode_change_error(
        current_mode="inherit", requested_mode="approve", job_label="job"
    )
    assert error is not None
    assert "yolo" in error.lower()


def test_interactive_grant_uses_job_scoped_approval_rule(monkeypatch):
    import tools.approval as approval
    from tools import approval_context
    from tools.cronjob_tools import _approval_mode_change_error

    captured = {}
    monkeypatch.setattr(approval, "_yolo_active", lambda: False)
    monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(approval, "_presence", lambda _cb=None: (None, True, False, False))

    def approve(tool_name, reason, **kwargs):
        captured.update(tool_name=tool_name, reason=reason, **kwargs)
        return {"approved": True}

    monkeypatch.setattr(approval, "request_tool_approval", approve)
    assert _approval_mode_change_error(
        current_mode="inherit", requested_mode="approve", job_label="job-a"
    ) is None
    assert captured["tool_name"] == "cronjob_manage"
    assert captured["rule_key"].startswith("cron:delegate-approval-authority:")
    assert "future runs" in captured["reason"]

def test_same_or_narrower_job_mode_change_does_not_request_approval(monkeypatch):
    import tools.approval as approval
    from tools.cronjob_tools import _approval_mode_change_error

    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *_a, **_kw: pytest.fail("same/narrower changes must not prompt"),
    )
    assert _approval_mode_change_error(
        current_mode="approve", requested_mode="approve", job_label="job"
    ) is None
    assert _approval_mode_change_error(
        current_mode="approve", requested_mode="deny", job_label="job"
    ) is None
    assert _approval_mode_change_error(
        current_mode="approve", requested_mode="inherit", job_label="job"
    ) is None


def test_explicit_operator_grant_does_not_reprompt(monkeypatch):
    import tools.approval as approval
    from tools.cronjob_tools import _approval_mode_change_error

    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *_a, **_kw: pytest.fail("explicit CLI grant must not prompt again"),
    )
    assert _approval_mode_change_error(
        current_mode="inherit",
        requested_mode="approve",
        job_label="job",
        preapproved=True,
    ) is None

def test_model_schema_and_job_view_expose_approval_mode():
    from tools.cronjob_job_args import _format_job
    from tools.cronjob_tools import CRONJOB_SCHEMA

    prop = CRONJOB_SCHEMA["parameters"]["properties"]["approval_mode"]
    assert prop["enum"] == ["inherit", "approve", "deny"]
    assert _format_job({"id": "j1", "prompt": "p"})["approval_mode"] == "inherit"
    assert _format_job({"id": "j2", "prompt": "p", "approval_mode": "approve"})["approval_mode"] == "approve"
