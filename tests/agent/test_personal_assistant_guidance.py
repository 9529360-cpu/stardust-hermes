from types import SimpleNamespace

from agent.system_prompt import _tool_guidance_block


def _agent(tool_names, *, worker_guidance=""):
    return SimpleNamespace(
        valid_tool_names=set(tool_names),
        _kanban_worker_guidance=worker_guidance,
    )


def test_product_facades_enable_personal_assistant_guidance():
    guidance = _tool_guidance_block(_agent({
        "background_task", "background_task_graph", "todo_list", "delegate_task", "contacts",
    }))

    assert guidance is not None
    assert "background_task(action='start')" in guidance
    assert "background_task_graph" in guidance
    assert "contacts(action='lookup')" in guidance
    assert "resolution.status is 'resolved'" in guidance
    assert "resolution.selected" in guidance
    assert "needs_channel_selection" in guidance
    assert "never guess or synthesize" in guidance
    assert "kanban_create" not in guidance


def test_hidden_kernel_tools_do_not_enable_coordinator_guidance():
    guidance = _tool_guidance_block(_agent({
        "kanban_create", "kanban_list", "todo_list", "delegate_task",
    }))

    assert not guidance or "# Personal assistant execution policy" not in guidance


def test_worker_protocol_wins_over_coordinator_guidance():
    guidance = _tool_guidance_block(_agent({
        "background_task", "background_task_graph", "todo_list", "delegate_task", "contacts",
    }, worker_guidance="# Worker-only protocol"))

    assert "# Worker-only protocol" in guidance
    assert "# Personal assistant execution policy" not in guidance
