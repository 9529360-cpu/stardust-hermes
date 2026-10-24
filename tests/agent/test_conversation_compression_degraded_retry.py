"""Budget-triggered degraded retry is bounded and preserves failed transcripts."""
from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor


@pytest.mark.parametrize('mode,enabled,expected_calls,aborted', [
    ('recover', True, 2, False), ('malformed', True, 2, True),
    ('provider', True, 2, False), ('malformed', False, 1, True),
    ('normal', True, 1, False),
])
def test_degraded_retry(mode, enabled, expected_calls, aborted):
    cc = ContextCompressor(model='test', config_context_length=1000000, quiet_mode=True,
                           protect_first_n=0, protect_last_n=2, abort_on_summary_failure=True)
    cc.require_continuity_fields = True
    cc.request_input_budget_tokens = 160000
    cc.degraded_retry_on_abort = enabled
    names = ('Current Request', 'Satisfied Conditions', 'Unsatisfied Conditions',
             'Evidence', 'Resource State', 'Next Step')
    structured = '\n'.join('## '+name+'\nUnknown' for name in names)
    plain = '\n'.join(name+': Unknown' for name in names)
    messages = [{'role': 'user' if i % 2 == 0 else 'assistant',
                 'content': f'turn {i}: '+'sample '*10000} for i in range(24)]
    def respond(prompt, started):
        if mode == 'provider' and stub.call_count == 1:
            raise RuntimeError('local auxiliary provider unavailable')
        if mode == 'normal':
            return structured
        if mode in ('recover', 'provider') and stub.call_count == 2:
            assert 'plain text' in prompt
            return plain
        return '## Goal\npartial checkpoint'
    with patch.object(cc, '_call_summary_llm', side_effect=respond) as stub:
        result = cc.compress(messages, current_tokens=200000)
    assert stub.call_count == expected_calls
    assert cc._last_compress_aborted is aborted
    if aborted:
        assert result == messages
        assert 'Incomplete continuity summary' in cc._last_summary_error
    else:
        assert len(result) < len(messages)
        assert cc._previous_summary
    assert cc.require_continuity_fields is True


def test_under_budget_abort_does_not_retry():
    cc = ContextCompressor(model='test', config_context_length=100000, quiet_mode=True,
                           protect_first_n=0, protect_last_n=2, abort_on_summary_failure=True)
    cc.require_continuity_fields = cc.degraded_retry_on_abort = True
    cc.request_input_budget_tokens = 160000
    messages = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'sample '*1000}
                for i in range(20)]
    with patch.object(cc, '_call_summary_llm', return_value='## Goal\npartial') as stub:
        assert cc.compress(messages, current_tokens=100000) == messages
    assert stub.call_count == 1


def test_degraded_flag_parsing():
    from types import SimpleNamespace
    from agent.agent_init import _parse_compression_config
    agent = SimpleNamespace(model='test', provider='custom')
    for value, expected in [(None, False), (False, False), ('false', False), (True, True)]:
        cfg = {} if value is None else {'degraded_retry_on_abort': value}
        assert _parse_compression_config(agent, {'compression': cfg}).degraded_retry_on_abort is expected


def test_budget_refusal_surfaces_auxiliary_reason():
    from tests.agent.test_request_input_budget import agent_for_budget, gate
    agent = agent_for_budget()
    agent.context_compressor.degraded_retry_on_abort = True
    agent.context_compressor._last_summary_error = 'local auxiliary provider unavailable'
    result = gate(agent, 1001, _preflight_compression_blocked=True)
    assert result.failed
    assert 'Compression failed: local auxiliary provider unavailable' in result.final_response
    agent._emit_status.assert_called_once_with(result.final_response)


def test_plain_empty_field_still_rejected():
    from agent.context_compressor_summary import validate_continuity_summary
    cc = object.__new__(ContextCompressor)
    text = 'Current Request:\nSatisfied Conditions: None'
    with pytest.raises(ValueError, match='Current Request'):
        validate_continuity_summary(cc._normalize_degraded_summary(text))
