"""Regression contracts for persist-before-project continuation admission.

The SQLite-backed test agent is an explicit test double for AIAgent persistence;
these tests do not claim end-to-end coverage of SessionDB or model transports.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent import turn_stop_gates as gates
from agent.turn_final_response import finish_text_response


class DurableAgent:
    def __init__(self, path, outcome=True, projection_error=False):
        self.path = path
        self.outcome = outcome
        self.projection_error = projection_error
        self.events = []
        self.projected = []
        self._verification_stop_nudges = 0
        self._pre_verify_nudges = 0
        self._incremental_persistence_failed = False
        self._last_persistence_error_cause = None
        self._turn_file_mutation_paths = {'src.py'}
        self._stall_guards = False
        self.valid_tool_names = {'terminal'}
        self.quiet_mode = True
        self.session_id = 'test-session'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE transcript (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)')

    def _flush_messages_to_session_db(self, messages, _history):
        self.events.append('flush')
        if self.outcome is False:
            self._last_persistence_error_cause = 'locked'
            return False
        if isinstance(self.outcome, Exception):
            raise self.outcome
        with sqlite3.connect(self.path) as conn:
            for position, message in enumerate(messages):
                conn.execute('INSERT OR REPLACE INTO transcript VALUES (?, ?)', (position, json.dumps(message)))
        return self.outcome

    def _emit_interim_assistant_message(self, message):
        self.events.append('emit')
        with sqlite3.connect(self.path) as conn:
            durable = [json.loads(row[0]) for row in conn.execute('SELECT payload FROM transcript ORDER BY position')]
        self.projected.append((dict(message), durable))
        if self.projection_error:
            raise RuntimeError('renderer disconnected')

    def _interim_content_was_streamed(self, text):
        return bool(self.projected) and not self.projection_error

    def _has_content_after_think_block(self, text):
        return bool(text)

    def _emit_pending_fallback_notice(self):
        pass

    def _clear_status_buffer(self):
        pass

    def _strip_think_blocks(self, text):
        return text

    def _looks_like_codex_intermediate_ack(self, **_kwargs):
        return True

    def _build_assistant_message(self, message, reason):
        return {'role': 'assistant', 'content': message.content, 'finish_reason': reason}


def select_gate(monkeypatch, kind):
    monkeypatch.setattr(gates, '_verify_on_stop_nudge', lambda _agent: 'run tests' if kind == 'verify' else None)
    monkeypatch.setattr(gates, '_pre_verify_nudge', lambda *_args: 'review once' if kind == 'hook' else None)


def gate_call(agent, messages):
    return gates.apply_stop_gates(
        agent, {'role': 'assistant', 'content': 'candidate answer'},
        final_response='candidate answer', messages=messages, conversation_history=[],
        pending_verification_response='older candidate', pending_verification_response_previewed=True,
    )


def final_call(agent, messages, **overrides):
    kwargs = dict(
        assistant_message=SimpleNamespace(content='candidate answer', tool_calls=[]),
        response=None, finish_reason='stop', messages=messages, api_messages=[],
        conversation_history=[], api_call_count=1, user_message='fix the defect',
        active_system_prompt='stable-prefix', final_response=None, _turn_exit_reason='',
        _preflight_compression_blocked=False, codex_ack_continuations=0,
        truncated_response_parts=[], length_continue_retries=0,
        _pending_verification_response='older candidate', _pending_verification_response_previewed=True,
    )
    kwargs.update(overrides)
    return finish_text_response(agent, **kwargs)


@pytest.fixture(autouse=True)
def disable_unrelated_model_guards(monkeypatch):
    from agent import agent_runtime_helpers
    monkeypatch.setattr(agent_runtime_helpers, 'intent_ack_continuation_mode', lambda _agent: 'off')
    monkeypatch.setattr(agent_runtime_helpers, 'trailing_continue_intent', lambda _text: False)


@pytest.mark.parametrize('kind', ['verify', 'hook'])
@pytest.mark.parametrize('outcome', [True, None], ids=['explicit-success', 'legacy-success'])
def test_projection_observes_already_committed_answer(tmp_path, monkeypatch, kind, outcome):
    select_gate(monkeypatch, kind)
    agent = DurableAgent(tmp_path / 'state.db', outcome)
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = gate_call(agent, messages)
    assert agent.events == ['flush', 'emit']
    assert verdict.continue_turn is True
    assert not getattr(verdict, 'persistence_failed', False)
    projected, durable = agent.projected[0]
    assert durable[-1] == projected
    assert [message['role'] for message in messages] == ['user', 'assistant', 'user']
    assert messages[-1].get('_verification_stop_synthetic' if kind == 'verify' else '_pre_verify_synthetic') is True
    assert verdict.pending_verification_response == 'candidate answer'
    assert agent._verification_stop_nudges + agent._pre_verify_nudges == 1


@pytest.mark.parametrize('kind', ['verify', 'hook'])
@pytest.mark.parametrize('outcome', [False, sqlite3.OperationalError('database is locked')], ids=['false', 'exception'])
def test_failed_persistence_never_admits_a_continuation(tmp_path, monkeypatch, kind, outcome):
    select_gate(monkeypatch, kind)
    agent = DurableAgent(tmp_path / 'state.db', outcome)
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = gate_call(agent, messages)
    assert verdict.continue_turn is False
    assert getattr(verdict, 'persistence_failed', False) is True
    assert verdict.final_response == ''
    assert verdict.pending_verification_response is None
    assert verdict.pending_verification_response_previewed is False
    assert agent._incremental_persistence_failed is True
    assert agent._last_persistence_error_cause == 'locked'
    assert agent.events == ['flush']
    assert agent.projected == []
    assert [message['role'] for message in messages] == ['user', 'assistant']
    assert agent._verification_stop_nudges + agent._pre_verify_nudges == 0


@pytest.mark.parametrize('kind', ['verify', 'hook'])
def test_projection_failure_does_not_replay_a_durable_answer(tmp_path, monkeypatch, kind):
    select_gate(monkeypatch, kind)
    agent = DurableAgent(tmp_path / 'state.db', projection_error=True)
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = gate_call(agent, messages)
    assert verdict.continue_turn is True
    assert agent.events == ['flush', 'emit']
    assert not agent._incremental_persistence_failed
    assert sum(message['role'] == 'assistant' for message in messages) == 1


@pytest.mark.parametrize('kind', ['verify', 'hook'])
@pytest.mark.parametrize('outcome', [False, sqlite3.OperationalError('database is locked')], ids=['false', 'exception'])
def test_final_response_phase_exits_failed_without_duplicate_append(tmp_path, monkeypatch, kind, outcome):
    select_gate(monkeypatch, kind)
    agent = DurableAgent(tmp_path / 'state.db', outcome)
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = final_call(agent, messages)
    assert verdict.action == 'break'
    assert getattr(verdict, 'failed', False) is True
    assert verdict._turn_exit_reason == 'session_persistence_failed'
    assert verdict.final_response == ''
    assert verdict._pending_verification_response is None
    assert verdict._pending_verification_response_previewed is False
    assert agent.events == ['flush']
    assert [message['role'] for message in messages] == ['user', 'assistant']
    assert verdict.active_system_prompt == 'stable-prefix'


@pytest.mark.parametrize('outcome', [False, sqlite3.OperationalError('database is locked')], ids=['false', 'exception'])
def test_intent_ack_uses_the_same_durability_gate(tmp_path, monkeypatch, outcome):
    from agent import agent_runtime_helpers
    select_gate(monkeypatch, None)
    monkeypatch.setattr(agent_runtime_helpers, 'intent_ack_continuation_mode', lambda _agent: 'all')
    agent = DurableAgent(tmp_path / 'state.db', outcome)
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = final_call(agent, messages)
    assert verdict.action == 'break'
    assert getattr(verdict, 'failed', False) is True
    assert verdict._turn_exit_reason == 'session_persistence_failed'
    assert verdict._pending_verification_response is None
    assert verdict._pending_verification_response_previewed is False
    assert agent.events == ['flush']
    assert verdict.codex_ack_continuations == 0
    assert [message['role'] for message in messages] == ['user', 'assistant']


def test_successful_intent_ack_keeps_existing_message_shape(tmp_path, monkeypatch):
    from agent import agent_runtime_helpers
    select_gate(monkeypatch, None)
    monkeypatch.setattr(agent_runtime_helpers, 'intent_ack_continuation_mode', lambda _agent: 'all')
    agent = DurableAgent(tmp_path / 'state.db')
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = final_call(agent, messages)
    assert verdict.action == 'continue'
    assert agent.events == ['flush', 'emit']
    assert verdict.codex_ack_continuations == 1
    assert [message['role'] for message in messages] == ['user', 'assistant', 'user']
    assert agent.projected[0][1][-1] == messages[-2]


def test_no_gate_keeps_normal_finalization(tmp_path, monkeypatch):
    select_gate(monkeypatch, None)
    agent = DurableAgent(tmp_path / 'state.db')
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = final_call(agent, messages)
    assert verdict.action == 'break'
    assert verdict.final_response == 'candidate answer'
    assert verdict._turn_exit_reason == 'text_response(finish_reason=stop)'
    assert agent.events == ['flush']
    assert [message['role'] for message in messages] == ['user', 'assistant']


def test_ephemeral_dropped_tool_scaffolding_is_not_made_durable(tmp_path, monkeypatch):
    select_gate(monkeypatch, None)
    agent = DurableAgent(tmp_path / 'state.db')
    agent.model, agent.provider = 'test-model', 'test-provider'
    agent._emit_status = lambda _text: None
    messages = [{'role': 'user', 'content': 'request'}]
    verdict = final_call(agent, messages, finish_reason='tool_calls')
    assert verdict.action == 'continue'
    assert agent.events == []
    assert all(message.get('_dropped_toolcall_nudge') for message in messages[-2:])


def test_existing_failed_state_survives_phase_projection(tmp_path, monkeypatch):
    select_gate(monkeypatch, None)
    agent = DurableAgent(tmp_path / 'state.db')
    verdict = final_call(agent, [{'role': 'user', 'content': 'request'}], failed=True)
    assert verdict.failed is True


def test_flush_false_without_cause_has_explicit_unknown(tmp_path, monkeypatch):
    select_gate(monkeypatch, 'verify')
    agent = DurableAgent(tmp_path / 'state.db')
    agent._flush_messages_to_session_db = lambda *_args: False
    verdict = gate_call(agent, [{'role': 'user', 'content': 'request'}])
    assert getattr(verdict, 'persistence_failed', False)
    assert agent._last_persistence_error_cause == 'unknown'
    assert not agent.projected
