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
    default_durability,
    default_rail,
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
