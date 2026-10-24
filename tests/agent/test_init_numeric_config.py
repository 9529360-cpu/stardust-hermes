"""Exercise the actual compression/turn-limit parsing seams, with no model client."""
import logging
import sys
from types import SimpleNamespace
import pytest
from agent.agent_init import _parse_compression_config, _apply_agent_section

FIELDS = {
    'threshold': ('threshold', 0.5), 'target_ratio': ('target_ratio', 0.2),
    'protect_last_n': ('protect_last', 20), 'protect_first_n': ('protect_first', 3),
    'idle_compact_after_seconds': ('idle_compact_after_seconds', 0),
    'max_attempts': ('max_attempts', 3), 'threshold_tokens': ('threshold_tokens', None),
    'request_input_budget_tokens': ('request_input_budget_tokens', 0),
    'min_tail_user_messages': ('min_tail_users', 1),
    'proactive_prune_tokens': ('proactive_prune_tokens', 0),
    'proactive_prune_min_result_chars': ('proactive_prune_min_chars', 8000),
    'proactive_prune_min_reclaim_tokens': ('proactive_prune_min_reclaim', 4096),
    'micro_compact_every_n_turns': ('micro_compact_every_n_turns', 1),
    'micro_compact_defrag_threshold_tokens': ('micro_compact_defrag_tokens', 2000),
    'codex_responses_compact_threshold': ('codex_responses_compact_threshold', None),
    'enabled': ('enabled', True), 'in_place': ('in_place', True),
    'micro_compact': ('micro_compact', False),
    'checkpoint_required': ('checkpoint_required', False),
    'abort_on_summary_failure': ('abort_on_summary_failure', False),
    'codex_responses_native': ('codex_responses_native', False),
    'codex_gpt55_autoraise_notice': ('autoraise_notice_enabled', True),
}

@pytest.mark.parametrize('key', FIELDS)
def test_bad_compression_scalar_uses_default_with_warning(key, caplog):
    agent = SimpleNamespace(model='local', provider='custom', api_mode='chat_completions')
    with caplog.at_level(logging.WARNING, logger='run_agent'):
        settings = _parse_compression_config(agent, {'compression': {key: 'banana'}})
    attr, default = FIELDS[key]
    assert getattr(settings, attr) == default
    assert any(f'compression.{key}' in r.message and r.levelno == logging.WARNING
               for r in caplog.records)

@pytest.mark.parametrize('value', [-1, 0, 1.1, float('nan'), float('inf'), True, None])
def test_invalid_threshold_range_falls_back(value, caplog):
    agent = SimpleNamespace(model='local', provider='custom')
    with caplog.at_level(logging.WARNING):
        settings = _parse_compression_config(agent, {'compression': {'threshold': value}})
    assert settings.threshold == 0.5
    assert 'compression.threshold' in caplog.text

@pytest.mark.parametrize('key,attr,default', [
    ('api_max_retries', '_api_max_retries', 3),
    ('run_budget_seconds', 'run_budget_seconds', None),
])
def test_invalid_agent_numeric_uses_documented_default(key, attr, default, caplog):
    agent = SimpleNamespace(run_budget_seconds=None)
    with caplog.at_level(logging.WARNING):
        _apply_agent_section(agent, {'agent': {key: 'banana', 'environment_probe': False}})
    assert getattr(agent, attr) == default
    assert f'agent.{key}' in caplog.text


def test_valid_compression_values_and_legacy_clamps_stay_identical():
    agent = SimpleNamespace(model='local', provider='custom')
    settings = _parse_compression_config(agent, {'compression': {
        'threshold': '0.65', 'target_ratio': '0.25', 'protect_last_n': '12',
        'protect_first_n': 0, 'max_attempts': 20, 'proactive_prune_tokens': -1,
    }})
    assert (settings.threshold, settings.target_ratio, settings.protect_last,
            settings.protect_first, settings.max_attempts, settings.proactive_prune_tokens) == (
                0.65, 0.25, 12, 0, 10, 0)


def test_turn_limit_boundary_recovers_before_iteration_budget(caplog):
    from unittest.mock import patch
    from run_agent import AIAgent

    cfg = {'agent': {'environment_probe': False}}
    with (
        patch('hermes_cli.config.load_config', return_value=cfg),
        patch('hermes_cli.config.load_config_readonly', return_value=cfg),
        patch('model_tools.get_tool_definitions', return_value=[]),
        patch('model_tools.check_toolset_requirements', return_value={}),
        patch('agent.process_bootstrap.OpenAI'),
        caplog.at_level(logging.WARNING),
    ):
        agent = AIAgent(model='local', api_key='local-test-placeholder',
                        base_url='http://127.0.0.1:1/v1', provider='custom',
                        max_iterations='banana', quiet_mode=True,
                        skip_context_files=True, skip_memory=True,
                        skip_background_review=True)
    try:
        assert agent.max_iterations == sys.maxsize
        assert agent.iteration_budget.remaining == sys.maxsize
        assert 'agent.max_turns' in caplog.text
    finally:
        agent.close()

