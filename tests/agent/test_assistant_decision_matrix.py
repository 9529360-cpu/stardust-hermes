import pytest

from agent.assistant_intent import (
    AssistantExecutionDecision,
    AssistantIntent,
    ExecutionDurability,
    ExecutionRail,
)


@pytest.mark.parametrize(
    ("intent", "rail", "durability"),
    [
        (AssistantIntent.RESPOND, ExecutionRail.NONE, ExecutionDurability.TURN),
        (AssistantIntent.CLARIFY, ExecutionRail.NONE, ExecutionDurability.TURN),
        (AssistantIntent.EXECUTE, ExecutionRail.CURRENT_SESSION, ExecutionDurability.TURN),
        (AssistantIntent.DELEGATE, ExecutionRail.DELEGATION, ExecutionDurability.TURN),
        (AssistantIntent.BACKGROUND, ExecutionRail.PROCESS, ExecutionDurability.PROCESS),
        (AssistantIntent.BACKGROUND, ExecutionRail.DELEGATION, ExecutionDurability.PROCESS),
        (AssistantIntent.SCHEDULE, ExecutionRail.CRON, ExecutionDurability.RESTART_SAFE),
        (AssistantIntent.SCHEDULE, ExecutionRail.KANBAN, ExecutionDurability.RESTART_SAFE),
    ],
)
def test_supported_execution_decision_matrix(intent, rail, durability):
    decision = AssistantExecutionDecision(intent=intent, rail=rail, durability=durability)
    assert decision.intent is intent
    assert decision.rail is rail
    assert decision.durability is durability


@pytest.mark.parametrize(
    ("intent", "rail", "durability", "message"),
    [
        (AssistantIntent.EXECUTE, ExecutionRail.DELEGATION, ExecutionDurability.TURN, "execute intent cannot select"),
        (AssistantIntent.DELEGATE, ExecutionRail.PROCESS, ExecutionDurability.TURN, "delegate intent cannot select"),
        (AssistantIntent.BACKGROUND, ExecutionRail.CRON, ExecutionDurability.PROCESS, "background intent cannot select"),
        (AssistantIntent.EXECUTE, ExecutionRail.CURRENT_SESSION, ExecutionDurability.PROCESS, "execute intent cannot use"),
        (AssistantIntent.DELEGATE, ExecutionRail.DELEGATION, ExecutionDurability.RESTART_SAFE, "delegate intent cannot use"),
    ],
)
def test_crossed_execution_decisions_fail_loudly(intent, rail, durability, message):
    with pytest.raises(ValueError, match=message):
        AssistantExecutionDecision(intent=intent, rail=rail, durability=durability)
