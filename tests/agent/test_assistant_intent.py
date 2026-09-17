import pytest

from agent.assistant_intent import (
    ATTENTION_TASK_STATES,
    TERMINAL_TASK_STATES,
    AssistantExecutionDecision,
    AssistantIntent,
    AssistantTaskProjection,
    ExecutionDurability,
    ExecutionRail,
    TaskLifecycleState,
    assistant_task_id,
    cron_job_lifecycle_state,
    default_durability,
    default_rail,
    delegation_lifecycle_state,
    kanban_lifecycle_state,
    project_cron_job,
    project_delegation,
    project_kanban_task,
    task_state_is_terminal,
    task_state_needs_attention,
)


def test_intent_wire_values_match_mission_vocabulary():
    assert [intent.value for intent in AssistantIntent] == [
        "respond",
        "execute",
        "delegate",
        "background",
        "schedule",
        "clarify",
    ]


def test_task_lifecycle_wire_values_cover_running_attention_and_terminal_states():
    assert [state.value for state in TaskLifecycleState] == [
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
        TaskLifecycleState.COMPLETED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.INTERRUPTED,
    }
    assert ATTENTION_TASK_STATES == {
        TaskLifecycleState.WAITING_FOR_USER,
        TaskLifecycleState.BLOCKED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.INTERRUPTED,
    }


def test_task_state_helpers_keep_waiting_nonterminal_and_interruption_visible():
    assert not task_state_is_terminal(TaskLifecycleState.WAITING_FOR_USER)
    assert task_state_needs_attention(TaskLifecycleState.WAITING_FOR_USER)
    assert not task_state_is_terminal(TaskLifecycleState.PAUSED)
    assert not task_state_needs_attention(TaskLifecycleState.PAUSED)
    assert task_state_is_terminal(TaskLifecycleState.INTERRUPTED)
    assert task_state_needs_attention(TaskLifecycleState.INTERRUPTED)
    assert task_state_is_terminal(TaskLifecycleState.COMPLETED)
    assert not task_state_needs_attention(TaskLifecycleState.COMPLETED)


def test_task_projection_reuses_owner_identity_without_becoming_state_owner():
    projection = AssistantTaskProjection(
        task_id="assistant-task-7",
        title="Research the library",
        state=TaskLifecycleState.RUNNING,
        rail=ExecutionRail.DELEGATION,
        durability=ExecutionDurability.PROCESS,
        parent_session_id="session-parent",
        owner_id="delegation-42",
        artifact_refs=("/tmp/report.md",),
    )

    assert projection.owner_id == "delegation-42"
    assert projection.parent_session_id == "session-parent"
    assert not projection.terminal
    assert not projection.needs_attention


def test_task_projection_surfaces_approval_and_terminal_attention_truthfully():
    waiting = AssistantTaskProjection(
        task_id="assistant-task-8",
        title="Deploy the release",
        state=TaskLifecycleState.WAITING_FOR_USER,
        rail=ExecutionRail.CURRENT_SESSION,
        durability=ExecutionDurability.TURN,
        requires_approval=True,
    )
    interrupted = AssistantTaskProjection(
        task_id="assistant-task-9",
        title="Background research",
        state=TaskLifecycleState.INTERRUPTED,
        rail=ExecutionRail.DELEGATION,
        durability=ExecutionDurability.PROCESS,
        recoverable=True,
    )

    assert not waiting.terminal
    assert waiting.needs_attention
    assert interrupted.terminal
    assert interrupted.needs_attention
    assert interrupted.recoverable


def test_task_projection_requires_real_owner_and_truthful_durability():
    with pytest.raises(ValueError, match="stable task id"):
        AssistantTaskProjection(
            task_id=" ",
            title="No identity",
            state=TaskLifecycleState.QUEUED,
            rail=ExecutionRail.DELEGATION,
            durability=ExecutionDurability.PROCESS,
        )

    with pytest.raises(ValueError, match="authoritative execution rail"):
        AssistantTaskProjection(
            task_id="assistant-task-10",
            title="No owner",
            state=TaskLifecycleState.QUEUED,
            rail=ExecutionRail.NONE,
            durability=ExecutionDurability.TURN,
        )

    with pytest.raises(ValueError, match="restart-safe task projections require cron or kanban ownership"):
        AssistantTaskProjection(
            task_id="assistant-task-11",
            title="Pretend durable delegation",
            state=TaskLifecycleState.RUNNING,
            rail=ExecutionRail.DELEGATION,
            durability=ExecutionDurability.RESTART_SAFE,
        )

    with pytest.raises(ValueError, match="cron/kanban task projections must be restart-safe"):
        AssistantTaskProjection(
            task_id="assistant-task-12",
            title="Pretend volatile cron",
            state=TaskLifecycleState.RUNNING,
            rail=ExecutionRail.CRON,
            durability=ExecutionDurability.PROCESS,
        )


def test_stable_task_ids_namespace_native_owner_ids_by_rail():
    assert assistant_task_id(ExecutionRail.DELEGATION, "42") == "delegation:42"
    assert assistant_task_id(ExecutionRail.CRON, "42") == "cron:42"
    assert assistant_task_id(ExecutionRail.KANBAN, "42") == "kanban:42"

    with pytest.raises(ValueError, match="execution rail"):
        assistant_task_id(ExecutionRail.NONE, "42")
    with pytest.raises(ValueError, match="owner id"):
        assistant_task_id(ExecutionRail.CRON, " ")


def test_delegation_owner_states_map_without_claiming_restart_durability():
    for native in ("dispatched", "running", "stalling", "finalizing"):
        assert delegation_lifecycle_state(native) is TaskLifecycleState.RUNNING
    for native in ("completed", "success", "ok"):
        assert delegation_lifecycle_state(native) is TaskLifecycleState.COMPLETED
    for native in ("failed", "error", "timeout", "stalled", "rejected"):
        assert delegation_lifecycle_state(native) is TaskLifecycleState.FAILED
    assert delegation_lifecycle_state("cancelled") is TaskLifecycleState.CANCELLED
    assert delegation_lifecycle_state("unknown") is TaskLifecycleState.INTERRUPTED

    with pytest.raises(ValueError, match="unknown delegation state"):
        delegation_lifecycle_state("new-future-state")


def test_kanban_owner_states_map_from_canonical_board_columns():
    for native in ("triage", "todo", "scheduled", "ready"):
        assert kanban_lifecycle_state(native) is TaskLifecycleState.QUEUED
    for native in ("running", "review"):
        assert kanban_lifecycle_state(native) is TaskLifecycleState.RUNNING
    assert kanban_lifecycle_state("blocked") is TaskLifecycleState.BLOCKED
    assert kanban_lifecycle_state("done") is TaskLifecycleState.COMPLETED
    assert kanban_lifecycle_state("archived") is TaskLifecycleState.COMPLETED

    with pytest.raises(ValueError, match="unknown kanban state"):
        kanban_lifecycle_state("failed")


def test_cron_job_state_preserves_paused_scheduled_and_active_semantics():
    assert cron_job_lifecycle_state(enabled=False) is TaskLifecycleState.PAUSED
    assert cron_job_lifecycle_state(enabled=True) is TaskLifecycleState.QUEUED
    assert cron_job_lifecycle_state(enabled=True, running=True) is TaskLifecycleState.RUNNING
    assert cron_job_lifecycle_state(enabled=False, running=True) is TaskLifecycleState.RUNNING


def test_owner_projection_adapters_keep_native_owner_as_authority():
    delegated = project_delegation(
        {
            "delegation_id": "d-1",
            "status": "unknown",
            "goal": "Research the library",
            "parent_session_id": "session-7",
            "error": "owner exited",
        }
    )
    cron = project_cron_job(
        {"id": "c-1", "name": "Morning inbox watch", "enabled": False, "paused_reason": "user paused"}
    )
    kanban = project_kanban_task(
        {"id": "k-1", "title": "Fix the bug", "status": "blocked", "blocked_reason": "needs credential"}
    )

    assert delegated.task_id == "delegation:d-1"
    assert delegated.owner_id == "d-1"
    assert delegated.parent_session_id == "session-7"
    assert delegated.state is TaskLifecycleState.INTERRUPTED
    assert delegated.durability is ExecutionDurability.PROCESS
    assert delegated.recoverable

    assert cron.task_id == "cron:c-1"
    assert cron.owner_id == "c-1"
    assert cron.state is TaskLifecycleState.PAUSED
    assert cron.durability is ExecutionDurability.RESTART_SAFE
    assert cron.detail == "user paused"

    assert kanban.task_id == "kanban:k-1"
    assert kanban.owner_id == "k-1"
    assert kanban.state is TaskLifecycleState.BLOCKED
    assert kanban.durability is ExecutionDurability.RESTART_SAFE
    assert kanban.needs_attention


def test_batch_delegation_projection_uses_bounded_human_title():
    projection = project_delegation(
        {
            "delegation_id": "d-batch",
            "status": "running",
            "goals": ["inspect API", "run tests", "write report"],
        }
    )
    assert projection.title == "3 delegated tasks"


def test_default_durability_keeps_background_process_local_and_schedule_restart_safe():
    assert default_durability(AssistantIntent.RESPOND) is ExecutionDurability.TURN
    assert default_durability(AssistantIntent.EXECUTE) is ExecutionDurability.TURN
    assert default_durability(AssistantIntent.DELEGATE) is ExecutionDurability.TURN
    assert default_durability(AssistantIntent.BACKGROUND) is ExecutionDurability.PROCESS
    assert default_durability(AssistantIntent.SCHEDULE) is ExecutionDurability.RESTART_SAFE


def test_default_rails_reuse_existing_runtime_owners():
    assert default_rail(AssistantIntent.RESPOND) is ExecutionRail.NONE
    assert default_rail(AssistantIntent.CLARIFY) is ExecutionRail.NONE
    assert default_rail(AssistantIntent.EXECUTE) is ExecutionRail.CURRENT_SESSION
    assert default_rail(AssistantIntent.DELEGATE) is ExecutionRail.DELEGATION
    assert default_rail(AssistantIntent.BACKGROUND) is ExecutionRail.DELEGATION
    assert default_rail(AssistantIntent.SCHEDULE) is ExecutionRail.CRON


def test_scheduled_work_cannot_masquerade_as_process_local_background_work():
    with pytest.raises(ValueError, match="scheduled work must use a restart-safe execution rail"):
        AssistantExecutionDecision(
            intent=AssistantIntent.SCHEDULE,
            durability=ExecutionDurability.PROCESS,
        )


def test_schedule_requires_an_existing_durable_owner():
    with pytest.raises(ValueError, match="scheduled work must select a durable execution rail"):
        AssistantExecutionDecision(
            intent=AssistantIntent.SCHEDULE,
            durability=ExecutionDurability.RESTART_SAFE,
            rail=ExecutionRail.DELEGATION,
        )

    decision = AssistantExecutionDecision(
        intent=AssistantIntent.SCHEDULE,
        durability=ExecutionDurability.RESTART_SAFE,
        rail=ExecutionRail.KANBAN,
        task_id="task-123",
    )
    assert decision.rail is ExecutionRail.KANBAN


def test_background_work_cannot_be_turn_scoped():
    with pytest.raises(ValueError, match="background work cannot be turn-scoped"):
        AssistantExecutionDecision(
            intent=AssistantIntent.BACKGROUND,
            durability=ExecutionDurability.TURN,
        )


def test_non_execution_turns_never_claim_task_or_execution_rail():
    for intent in (AssistantIntent.RESPOND, AssistantIntent.CLARIFY):
        with pytest.raises(ValueError, match="non-execution decisions cannot own a task id"):
            AssistantExecutionDecision(
                intent=intent,
                durability=ExecutionDurability.TURN,
                task_id="task-123",
            )

        with pytest.raises(ValueError, match="non-execution decisions cannot select an execution rail"):
            AssistantExecutionDecision(
                intent=intent,
                durability=ExecutionDurability.TURN,
                rail=ExecutionRail.CURRENT_SESSION,
            )
