from agent.assistant_intent import (
    ASSISTANT_EXECUTION_GUIDANCE,
    EXECUTION_CONTRACTS,
    AssistantExecutionMode,
    contract_for,
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
    assert "must not steal focus" in ASSISTANT_EXECUTION_GUIDANCE
