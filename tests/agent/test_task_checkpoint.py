"""Bounded checkpoint behavior against real session persistence and process restart."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def context(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    for key in ('HOME', 'HERMES_HOME', 'STARDUST_HOME', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv('HERMES_PROFILE', 'default')
    (home / 'config.yaml').write_text('agent:\n  task_checkpoint: true\n  bind_acceptance_observations: true\n')
    from hermes_state import SessionDB
    from tools.todo_tool import TodoStore
    from agent import task_acceptance
    db = SessionDB()
    db.create_session(session_id='checkpoint-session', source='cli')
    task_acceptance.begin('checkpoint-session', 'Acceptance criteria:\n- [runtime] Write three files', tmp_path)
    agent = SimpleNamespace(session_id='checkpoint-session', _session_db=db, _todo_store=TodoStore())
    yield agent, home
    db.close()


def test_real_restart_resumes_pending_step(context, tmp_path):
    from agent import task_checkpoint as checkpoint
    from agent import acceptance_observations
    from tools.todo_tool import persist_todo_session_state
    agent, home = context
    checkpoint.prepare(agent, 'Acceptance criteria:\n- [runtime] Write three files')
    agent._todo_store.write([dict(id=str(i), content=f'Write {i}.txt', status='pending') for i in range(1, 4)])
    for i in (1, 2):
        artifact = tmp_path / f'{i}.txt'
        artifact.write_text(f'actual file {i}')
        call = f'write-{i}'
        rows = [dict(role='tool', content=json.dumps({'path': str(artifact)}), tool_name='write_file', tool_call_id=call)]
        agent._session_db.append_messages_batch(agent.session_id, rows)
        acceptance_observations.record(agent, {'_row_id': rows[0]['_row_id']}, 'write_file',
                                       {'path': str(artifact)}, {}, call, 'task')
        agent._todo_store.write([{'id': str(i), 'status': 'completed'}], merge=True)
    persist_todo_session_state(agent._session_db, agent.session_id, agent._todo_store)
    checkpoint.persist(agent)
    before = [(tmp_path / f'{i}.txt').stat().st_mtime_ns for i in (1, 2)]
    code = '''
import json
from types import SimpleNamespace
from hermes_state import SessionDB
from tools.todo_tool import TodoStore, load_todo_session_state
from agent.task_checkpoint import prepare
from agent.task_acceptance import begin
from pathlib import Path
with SessionDB() as db:
    state = load_todo_session_state(db, 'checkpoint-session')
    store = TodoStore()
    store.restore(state['todos'], revision=state['revision'])
    agent = SimpleNamespace(session_id='checkpoint-session', _session_db=db, _todo_store=store)
    begin(agent.session_id, 'continue', Path.cwd())
    context = prepare(agent, 'continue')
    print(context)
    checkpoint = db.get_session_model_config_value(agent.session_id, '_task_checkpoint_v1')
    assert checkpoint['next_step']['id'] == '3'
    # Exercise the pending file action and commit its actual result in the resumed process.
    root = Path(__import__('os').environ['CHECKPOINT_WORKSPACE'])
    artifact = root / '3.txt'
    artifact.write_text('actual file 3')
    db.append_messages_batch(agent.session_id, [dict(role='tool', tool_name='write_file',
        tool_call_id='write-3', content=json.dumps({'path': str(artifact)}))])
    store.write([{'id': '3', 'status': 'completed'}], merge=True)
    from tools.todo_tool import persist_todo_session_state
    persist_todo_session_state(db, agent.session_id, store)
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                            env={**os.environ, 'CHECKPOINT_WORKSPACE': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert '"id":"3"' in result.stdout
    assert 'snapshot' in result.stdout.lower() and 'not fresh authorization' in result.stdout
    assert 'message:' in result.stdout
    assert before == [(tmp_path / f'{i}.txt').stat().st_mtime_ns for i in (1, 2)]
    assert (tmp_path / '3.txt').read_text() == 'actual file 3'
    rows = agent._session_db.get_messages(agent.session_id)
    assert [r['tool_call_id'] for r in rows if r['role'] == 'tool'] == ['write-1', 'write-2', 'write-3']


def test_suspension_scope_competing_owner_and_bound(context, monkeypatch):
    from agent import task_checkpoint as checkpoint
    from agent import task_acceptance
    agent, home = context
    checkpoint.prepare(agent, 'Acceptance criteria:\n- [runtime] Write three files')
    agent._todo_store.write([dict(id=str(i), content='long task ' * 500, status='pending') for i in range(256)])
    from tools.todo_tool import persist_todo_session_state
    persist_todo_session_state(agent._session_db, agent.session_id, agent._todo_store)
    checkpoint.persist(agent)
    raw = agent._session_db.get_session_model_config_value(agent.session_id, checkpoint.KEY)
    assert len(json.dumps(raw, ensure_ascii=False).encode()) <= checkpoint.MAX_BYTES
    other = SimpleNamespace(session_id='wrong-session', _session_db=agent._session_db, _todo_store=agent._todo_store)
    assert checkpoint.reason(other, raw) == 'session owner mismatch'
    raw['task_id'] = 'competing-task'
    assert checkpoint.reason(agent, raw) == 'task owner mismatch'
    monkeypatch.setenv('STARDUST_HOME', str(home / 'profiles' / 'different'))
    assert checkpoint.reason(agent, raw) == 'HOME/profile scope mismatch'
    monkeypatch.setenv('STARDUST_HOME', str(home))
    task_acceptance.begin(agent.session_id, 'Explain photosynthesis', home)
    text = checkpoint.prepare(agent, 'Explain photosynthesis')
    assert 'suspended' in text and 'next_step' not in text
    checkpoint.persist(agent)
    assert 'suspended' in checkpoint.reference(agent)


def test_compression_reference_refreshes_without_accumulating(context):
    from agent import task_checkpoint as checkpoint
    from agent.conversation_compression import _fold_todo_snapshot
    from tools.todo_tool import persist_todo_session_state
    agent, home = context
    checkpoint.prepare(agent, 'Acceptance criteria:\n- [runtime] Write three files')
    agent._todo_store.write([dict(id='3', content='Write third file', status='pending')])
    persist_todo_session_state(agent._session_db, agent.session_id, agent._todo_store)
    messages = [{'role': 'user', 'content': 'continue'}]
    _fold_todo_snapshot(agent, messages)
    _fold_todo_snapshot(agent, messages)
    assert str(messages).count(checkpoint.HEADER) == 1
    child_config = {}
    checkpoint.carry(agent, agent.session_id, 'child', child_config)
    assert child_config[checkpoint.KEY]['session_id'] == 'child'
    assert child_config[checkpoint.KEY]['source_session_id'] == agent.session_id
    from agent.task_acceptance import carry
    from tools.todo_tool import TODO_SESSION_STATE_KEY
    child_config[TODO_SESSION_STATE_KEY] = agent._todo_store.snapshot()
    agent._session_db.create_session(session_id='child', source='cli', model_config=child_config)
    carry(agent.session_id, 'child')
    child = SimpleNamespace(session_id='child', _session_db=agent._session_db, _todo_store=agent._todo_store)
    assert 'Not applied' not in checkpoint.prepare(child, 'continue')
    assert 'Write three files' in checkpoint.reference(child)


def test_stale_revision_is_not_applied(context):
    from agent import task_checkpoint as checkpoint
    from tools.todo_tool import persist_todo_session_state
    agent, home = context
    checkpoint.prepare(agent, 'Acceptance criteria:\n- [runtime] Write three files')
    agent._todo_store.write([dict(id='3', content='Write third file', status='pending')])
    persist_todo_session_state(agent._session_db, agent.session_id, agent._todo_store)
    checkpoint.persist(agent)
    agent._todo_store.write([{'id': '3', 'status': 'completed'}], merge=True)
    persist_todo_session_state(agent._session_db, agent.session_id, agent._todo_store)
    assert 'stale todo revision' in checkpoint.reference(agent)


def test_disabled_writes_nothing(context):
    from agent import task_checkpoint as checkpoint
    agent, home = context
    (home / 'config.yaml').write_text('agent:\n  task_checkpoint: false\n')
    before = agent._session_db.get_session(agent.session_id)['model_config']
    assert checkpoint.prepare(agent, 'continue') == ''
    checkpoint.persist(agent)
    from agent.session_persistence import SessionPersistenceMixin
    class PersistenceHarness(SessionPersistenceMixin):
        def _drop_trailing_empty_response_scaffolding(self, messages):
            pass
        def _flush_messages_to_session_db(self, messages, history):
            self._session_db.append_messages_batch(self.session_id, messages)
            return True
    harness = PersistenceHarness()
    harness.session_id = agent.session_id
    harness._session_db = agent._session_db
    harness._persist_session([{'role': 'assistant', 'content': 'pending'}])
    assert agent._session_db.get_session(agent.session_id)['model_config'] == before
