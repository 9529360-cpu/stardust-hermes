"""Internal stop continuations stay live without becoming recalled user input."""
import pytest

from agent.session_persistence import _db_flush_collect
from agent.turn_finalizer import _drop_verification_continuation_scaffolding
from tests.agent.test_continuation_durability import DurableAgent, final_call


@pytest.mark.parametrize('kind', ['ack', 'refusal'])
def test_internal_continuation_is_not_a_durable_user_turn(tmp_path, monkeypatch, kind):
    from agent import agent_runtime_helpers, capability_refusal_guard
    from hermes_state import SessionDB

    monkeypatch.setattr(agent_runtime_helpers, 'intent_ack_continuation_mode',
                        lambda _a: 'always' if kind == 'ack' else 'off')
    monkeypatch.setattr(agent_runtime_helpers, 'trailing_continue_intent', lambda _t: False)
    monkeypatch.setattr(capability_refusal_guard, 'should_nudge_capability_refusal',
                        lambda *_a: 'read_file' if kind == 'refusal' else None)
    agent = DurableAgent(tmp_path / 'projection.db')
    # A real user quoting the same prefix must remain durable.
    user = {'role': 'user', 'content': '[System: quoted by the user]'}
    messages = [user]
    verdict = final_call(agent, messages)
    assert verdict.action == 'continue'
    nudge = messages[-1]
    assert nudge['role'] == 'user'
    assert nudge['content']
    rows, _ = _db_flush_collect(agent, messages, [])
    commentary = [row for row in rows if row['role'] == 'assistant']
    assert commentary[-1]['content'] == 'candidate answer'
    assert commentary[-1]['display_metadata']['interim_commentary'] is True
    with SessionDB(tmp_path / 'state.db') as db:
        db.create_session(agent.session_id, source='cli')
        db.append_messages_batch(agent.session_id, rows)
        assert [m['content'] for m in db.get_messages(agent.session_id)
                if m['role'] == 'user'] == [user['content']]
        assert [m['content'] for m in db.get_messages_as_conversation(agent.session_id)
                if m['role'] == 'user'] == [user['content']]
        assert not db.search_messages('"Continue now"', role_filter=['user'])
        assert not db.search_messages('"capability note"', role_filter=['user'])
    _drop_verification_continuation_scaffolding(messages)
    assert nudge not in messages
    assert user in messages
