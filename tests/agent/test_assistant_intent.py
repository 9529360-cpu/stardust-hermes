import pytest

from agent.assistant_intent import (
    ASSISTANT_EXECUTION_GUIDANCE,
    ATTENTION_TASK_STATES,
    EXECUTION_CONTRACTS,
    TERMINAL_TASK_STATES,
    AssistantExecutionMode,
    AssistantExecutionRail,
    AssistantTaskProjection,
    AssistantTaskState,
    assistant_task_id,
    contract_for,
    cron_job_lifecycle_state,
    delegation_lifecycle_state,
    kanban_lifecycle_state,
    process_lifecycle_state,
    project_cron_job,
    project_delegation,
    project_kanban_task,
    project_process,
    rails_for,
)


def test_execution_policy_has_exact_product_modes():
    assert [contract.mode for contract in EXECUTION_CONTRACTS] == list(AssistantExecutionMode)


def test_background_delegation_is_explicitly_process_local():
    contract = contract_for(AssistantExecutionMode.DELEGATE_BACKGROUND)

    assert contract.background is True
    assert contract.durability == "process_local"
    assert "never promise" in contract.description


def test_schedule_or_watch_is_restart_durable():
    contract = contract_for(AssistantExecutionMode.SCHEDULE_OR_WATCH)

    assert contract.background is True
    assert contract.durability == "restart_durable"
    assert "cron" in contract.description
    assert "kanban" in contract.description


def test_clarify_is_reserved_for_material_user_decisions():
    contract = contract_for(AssistantExecutionMode.CLARIFY)

    assert contract.durability == "turn"
    assert "material user decision" in contract.description
    assert "authorization" in contract.description


def test_prompt_guidance_names_durability_and_focus_boundaries():
    assert "never as a promise of restart durability" in ASSISTANT_EXECUTION_GUIDANCE
    assert "multiple independent action requests" in ASSISTANT_EXECUTION_GUIDANCE
    assert "`assistant_tasks`" in ASSISTANT_EXECUTION_GUIDANCE
    assert "use cron for work whose defining requirement is a future time or recurrence" in ASSISTANT_EXECUTION_GUIDANCE
    assert "durable human-approval boundary" in ASSISTANT_EXECUTION_GUIDANCE
    assert "must not steal focus" in ASSISTANT_EXECUTION_GUIDANCE


def test_modes_map_only_to_existing_runtime_owners():
    assert rails_for(AssistantExecutionMode.RESPOND) == {AssistantExecutionRail.NONE}
    assert rails_for(AssistantExecutionMode.CLARIFY) == {AssistantExecutionRail.NONE}
    assert rails_for(AssistantExecutionMode.EXECUTE_FOREGROUND) == {
        AssistantExecutionRail.CURRENT_SESSION
    }
    assert rails_for(AssistantExecutionMode.DELEGATE) == {
        AssistantExecutionRail.DELEGATION
    }
    assert rails_for(AssistantExecutionMode.DELEGATE_BACKGROUND) == {
        AssistantExecutionRail.PROCESS,
        AssistantExecutionRail.DELEGATION,
    }
    assert rails_for(AssistantExecutionMode.SCHEDULE_OR_WATCH) == {
        AssistantExecutionRail.CRON,
        AssistantExecutionRail.KANBAN,
    }


def test_task_lifecycle_covers_attention_and_terminal_outcomes():
    assert [state.value for state in AssistantTaskState] == [
        "queued",
        "paused",
        "running",
        "waiting_for_user",
        "blocked",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ]
    assert TERMINAL_TASK_STATES == {
        AssistantTaskState.COMPLETED,
        AssistantTaskState.FAILED,
        AssistantTaskState.CANCELLED,
        AssistantTaskState.INTERRUPTED,
    }
    assert ATTENTION_TASK_STATES == {
        AssistantTaskState.WAITING_FOR_USER,
        AssistantTaskState.BLOCKED,
        AssistantTaskState.FAILED,
        AssistantTaskState.INTERRUPTED,
    }


def test_stable_task_ids_namespace_native_owner_ids_by_rail():
    assert assistant_task_id(AssistantExecutionRail.PROCESS, "42") == "process:42"
    assert assistant_task_id(AssistantExecutionRail.DELEGATION, "42") == "delegation:42"
    assert assistant_task_id(AssistantExecutionRail.CRON, "42") == "cron:42"
    assert assistant_task_id(AssistantExecutionRail.KANBAN, "42") == "kanban:42"

    with pytest.raises(ValueError, match="execution rail"):
        assistant_task_id(AssistantExecutionRail.NONE, "42")
    with pytest.raises(ValueError, match="owner id"):
        assistant_task_id(AssistantExecutionRail.CRON, " ")


def test_projection_enforces_owner_durability_instead_of_inventing_state():
    with pytest.raises(ValueError, match="process_local"):
        AssistantTaskProjection(
            task_id="process:p1",
            title="Pretend durable",
            state=AssistantTaskState.RUNNING,
            rail=AssistantExecutionRail.PROCESS,
            durability="restart_durable",
            background=True,
            owner_id="p1",
        )

    with pytest.raises(ValueError, match="background work"):
        AssistantTaskProjection(
            task_id="cron:c1",
            title="Pretend foreground cron",
            state=AssistantTaskState.QUEUED,
            rail=AssistantExecutionRail.CRON,
            durability="restart_durable",
            background=False,
            owner_id="c1",
        )


def test_delegation_states_keep_owner_loss_distinct_from_failure():
    for native in ("dispatched", "running", "stalling", "finalizing"):
        assert delegation_lifecycle_state(native) is AssistantTaskState.RUNNING
    for native in ("completed", "success", "ok"):
        assert delegation_lifecycle_state(native) is AssistantTaskState.COMPLETED
    for native in ("failed", "error", "timeout", "stalled", "rejected"):
        assert delegation_lifecycle_state(native) is AssistantTaskState.FAILED
    assert delegation_lifecycle_state("cancelled") is AssistantTaskState.CANCELLED
    assert delegation_lifecycle_state("unknown") is AssistantTaskState.INTERRUPTED

    with pytest.raises(ValueError, match="unknown delegation state"):
        delegation_lifecycle_state("future-state")


def test_process_states_preserve_exit_reason_and_code():
    assert process_lifecycle_state("running") is AssistantTaskState.RUNNING
    assert process_lifecycle_state("exited", exit_code=0) is AssistantTaskState.COMPLETED
    assert process_lifecycle_state("exited", exit_code=7) is AssistantTaskState.FAILED
    assert process_lifecycle_state("exited", exit_code=None) is AssistantTaskState.INTERRUPTED
    assert process_lifecycle_state(
        "exited", exit_code=0, completion_reason="killed"
    ) is AssistantTaskState.CANCELLED
    assert process_lifecycle_state(
        "exited", exit_code=-1, completion_reason="lost"
    ) is AssistantTaskState.INTERRUPTED
    assert process_lifecycle_state(
        "exited", exit_code=-1, completion_reason="failed_start"
    ) is AssistantTaskState.FAILED

    with pytest.raises(ValueError, match="invalid process exit code"):
        process_lifecycle_state("exited", exit_code="bad")
    with pytest.raises(ValueError, match="unknown process state"):
        process_lifecycle_state("future-state", exit_code=0)


def test_cron_states_include_terminal_scheduler_results():
    assert cron_job_lifecycle_state("scheduled") is AssistantTaskState.QUEUED
    assert cron_job_lifecycle_state("paused", enabled=False) is AssistantTaskState.PAUSED
    assert cron_job_lifecycle_state("completed") is AssistantTaskState.COMPLETED
    assert cron_job_lifecycle_state("error") is AssistantTaskState.FAILED
    assert cron_job_lifecycle_state(
        "paused", enabled=False, running=True
    ) is AssistantTaskState.RUNNING

    with pytest.raises(ValueError, match="unknown cron state"):
        cron_job_lifecycle_state("future-state")


def test_kanban_states_map_from_canonical_board_statuses():
    for native in ("triage", "todo", "scheduled", "ready"):
        assert kanban_lifecycle_state(native) is AssistantTaskState.QUEUED
    for native in ("running", "review"):
        assert kanban_lifecycle_state(native) is AssistantTaskState.RUNNING
    assert kanban_lifecycle_state("blocked") is AssistantTaskState.BLOCKED
    assert kanban_lifecycle_state("done") is AssistantTaskState.COMPLETED
    assert kanban_lifecycle_state("archived") is AssistantTaskState.COMPLETED

    with pytest.raises(ValueError, match="unknown kanban state"):
        kanban_lifecycle_state("failed")


def test_durable_delegation_projection_preserves_recovery_provenance():
    projection = project_delegation(
        {
            "delegation_id": "d-1",
            "state": "unknown",
            "parent_session_id": "parent-7",
            "task": {
                "goal": "Research the library",
                "project_id": "proj-1",
                "workspace_path": "/workspace/repo",
            },
            "event": {
                "status": "unknown",
                "error": "owner exited before terminal result",
                "live_transcripts": ["/cache/live/task-0.log"],
            },
            "result": {
                "summary": "partial findings",
                "files_written": ["/workspace/repo/report.md"],
            },
        }
    )

    assert projection.task_id == "delegation:d-1"
    assert projection.state is AssistantTaskState.INTERRUPTED
    assert projection.durability == "process_local"
    assert projection.parent_session_id == "parent-7"
    assert projection.project_ref == "proj-1"
    assert projection.workspace == "/workspace/repo"
    assert projection.final_report == "partial findings"
    assert projection.artifact_refs == (
        "/workspace/repo/report.md",
        "/cache/live/task-0.log",
    )
    assert projection.recoverable
    assert projection.recovery_action == "retry"
    assert projection.terminal
    assert projection.needs_attention


def test_process_projection_keeps_parent_workspace_and_lost_recovery():
    projection = project_process(
        {
            "session_id": "proc-1",
            "command": "python report.py",
            "status": "exited",
            "exit_code": -1,
            "completion_reason": "lost",
            "termination_source": "backend_lost",
            "parent_session_id": "parent-7",
            "cwd": "/workspace/repo",
            "handoff_note": "long report build",
        }
    )

    assert projection.task_id == "process:proc-1"
    assert projection.state is AssistantTaskState.INTERRUPTED
    assert projection.parent_session_id == "parent-7"
    assert projection.workspace == "/workspace/repo"
    assert projection.detail == "long report build"
    assert projection.recoverable
    assert projection.recovery_action == "retry"


def test_cron_projection_is_restart_durable_and_preserves_workdir():
    failed = project_cron_job(
        {
            "job_id": "cron-1",
            "name": "Morning inbox watch",
            "state": "error",
            "enabled": True,
            "last_error": "mail provider unavailable",
            "workdir": "/workspace/mail",
        }
    )
    completed = project_cron_job(
        {
            "job_id": "cron-2",
            "name": "One-shot export",
            "state": "completed",
            "enabled": False,
            "last_report": "export saved",
        }
    )

    assert failed.task_id == "cron:cron-1"
    assert failed.state is AssistantTaskState.FAILED
    assert failed.durability == "restart_durable"
    assert failed.workspace == "/workspace/mail"
    assert failed.recoverable
    assert failed.recovery_action == "review_or_retry"
    assert completed.state is AssistantTaskState.COMPLETED
    assert completed.final_report == "export saved"


def test_kanban_projection_preserves_project_workspace_artifacts_and_report():
    projection = project_kanban_task(
        {
            "id": "k-1",
            "title": "Fix the bug",
            "status": "done",
            "session_id": "parent-7",
            "project_id": "project-42",
            "workspace_path": "/repo/.worktrees/k-1",
            "result": "task row result",
        },
        run={
            "summary": "fixed and verified",
            "metadata": {
                "artifacts": ["/repo/report.txt"],
                "approval_refs": ["approval-9"],
            },
        },
    )

    assert projection.task_id == "kanban:k-1"
    assert projection.state is AssistantTaskState.COMPLETED
    assert projection.durability == "restart_durable"
    assert projection.parent_session_id == "parent-7"
    assert projection.project_ref == "project-42"
    assert projection.workspace == "/repo/.worktrees/k-1"
    assert projection.artifact_refs == ("/repo/report.txt",)
    assert projection.approval_refs == ("approval-9",)
    assert projection.final_report == "fixed and verified"


def test_projection_wire_shape_keeps_provenance_and_attention_machine_readable():
    projection = AssistantTaskProjection(
        task_id="current_session:s-1",
        title="Approve deployment",
        state=AssistantTaskState.WAITING_FOR_USER,
        rail=AssistantExecutionRail.CURRENT_SESSION,
        durability="turn",
        background=False,
        owner_id="s-1",
        parent_session_id="s-1",
        requires_approval=True,
        approval_refs=("approval-1",),
        artifact_refs=("diff://42",),
    )

    wire = projection.to_wire()
    assert wire["task_id"] == "current_session:s-1"
    assert wire["state"] == "waiting_for_user"
    assert wire["approval_refs"] == ["approval-1"]
    assert wire["artifact_refs"] == ["diff://42"]
    assert wire["terminal"] is False
    assert wire["needs_attention"] is True
