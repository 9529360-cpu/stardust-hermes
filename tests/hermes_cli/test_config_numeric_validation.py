"""Field-level diagnostics for numeric settings consumed during Agent construction."""
import copy
import pytest
from hermes_cli.config import DEFAULT_CONFIG, validate_config_structure

NUMERIC = [
    'threshold', 'target_ratio', 'protect_last_n', 'protect_first_n',
    'idle_compact_after_seconds', 'max_attempts', 'threshold_tokens',
    'request_input_budget_tokens', 'min_tail_user_messages', 'proactive_prune_tokens',
    'proactive_prune_min_result_chars', 'proactive_prune_min_reclaim_tokens',
    'micro_compact_every_n_turns', 'micro_compact_defrag_threshold_tokens',
    'codex_responses_compact_threshold',
]
FLAGS = ['enabled', 'codex_gpt55_autoraise', 'codex_gpt55_autoraise_notice',
         'abort_on_summary_failure', 'checkpoint_required', 'in_place',
         'micro_compact', 'codex_responses_native']

@pytest.mark.parametrize('section,key', [('compression', k) for k in NUMERIC + FLAGS] +
                         [('agent', k) for k in ['max_turns', 'api_max_retries', 'run_budget_seconds']])
def test_bad_scalar_has_one_actionable_issue_without_mutation(section, key):
    config = {section: {key: 'banana'}}
    before = copy.deepcopy(config)
    issues = validate_config_structure(config)
    assert len(issues) == 1
    assert f'{section}.{key}' in issues[0].message
    assert 'banana' in issues[0].message
    assert 'expected' in issues[0].message
    assert config == before

@pytest.mark.parametrize('value', [-1, 0, 1.1, float('nan'), float('inf'), True, None, [], {}])
def test_threshold_requires_finite_fraction(value):
    assert len(validate_config_structure({'compression': {'threshold': value}})) == 1

@pytest.mark.parametrize('section', ['agent', 'compression'])
def test_malformed_section_is_reported_without_dumping_contents(section):
    issues = validate_config_structure({section: 'broken'})
    assert len(issues) == 1
    assert section in issues[0].message
    assert 'mapping' in issues[0].message

@pytest.mark.parametrize('value', ['banana', -0.1, float('nan'), float('inf'), True])
def test_model_thresholds_are_validated_individually(value):
    issues = validate_config_structure({'compression': {'model_thresholds': {'local': value}}})
    assert len(issues) == 1
    assert 'compression.model_thresholds.local' in issues[0].message

@pytest.mark.parametrize('value', [None, 0, -1, -5, 'none', 'unlimited', 'inf', '∞', '0', '-1', '42', '42.0', 42])
def test_documented_turn_limit_spellings_stay_valid(value):
    assert validate_config_structure({'agent': {'max_turns': value}}) == []


def test_defaults_and_compatible_numeric_strings_are_unchanged():
    config = copy.deepcopy(DEFAULT_CONFIG)
    assert validate_config_structure(config) == []
    config['compression'].update(threshold='0.65', protect_last_n='12',
                                 proactive_prune_tokens=-1, max_attempts=20,
                                 request_input_budget_tokens=0)
    config['agent'].update(max_turns=None, run_budget_seconds=None)
    before = copy.deepcopy(config)
    assert validate_config_structure(config) == []
    assert config == before
