import pytest

from agent.assistant_intent import (
    AssistantExecutionDecision,
    AssistantIntent,
    ExecutionDurability,
    ExecutionRail,
    default_durability,
    default_rail,
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
