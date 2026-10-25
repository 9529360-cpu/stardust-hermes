"""Session initialization schedules semantic warm only for opted-in sessions."""
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("skip_memory", [False, True])
def test_agent_init_schedules_semantic_warm(enabled, skip_memory):
    cfg = {"memory": {"semantic_recall": enabled, "provider": ""}, "agent": {}}
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.load_config_readonly", return_value=cfg),
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
        patch("tools.memory_semantic.schedule_session_warm") as warm,
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="local-test-placeholder",
            base_url="http://127.0.0.1:9/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=skip_memory,
        )
        try:
            assert warm.call_count == int(enabled and not skip_memory)
        finally:
            agent.close()
