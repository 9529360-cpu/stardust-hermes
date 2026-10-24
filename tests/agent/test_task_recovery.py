"""DL-P2.4 deterministic boundary tests (no external model)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB


@pytest.fixture
def owner(tmp_path, monkeypatch):
    import agent.task_recovery as recovery
    for key in ('HOME', 'USERPROFILE', 'HERMES_HOME', 'STARDUST_HOME'):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(recovery, 'enabled', lambda: True)
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('recovery', source='cli', model='local')
    agent = SimpleNamespace(session_id='recovery', _session_db=db,
                            identity={'status': 'ready', 'individual_id': 'identity-a'})
    recovery.prepare(agent, 'write the report', task_id='task-a')
    yield agent, recovery
    db.close()


def test_last_committed_step_and_unknown_effect(owner):
    agent, recovery = owner
    recovery.start_step(agent, 'one', 'write_file', {'path': 'a', 'content': 'a'})
    recovery.commit_step(agent, 'one', 'done')
    recovery.start_step(agent, 'two', 'terminal', {'command': 'send'})
    point = recovery.find_resume_point(agent, task_id='task-a')
    assert point['last_committed_step'] == 1
    assert point['status'] == 'needs_review'
    assert point['pending'][0]['side_effect_ref']
    recovery.prepare(agent, 'continue', task_id='task-a')
    assert recovery.before_dispatch(agent, 'three', 'terminal', {'command': 'send'})['status'] == 'needs_review'
    assert recovery.before_dispatch(agent, 'four', 'read_file', {'path': 'a'}) is None


@pytest.mark.parametrize('dimension', ['home', 'profile', 'task', 'identity'])
@pytest.mark.parametrize('attempt', range(3))
def test_foreign_owner_executes_nothing(owner, monkeypatch, dimension, attempt):
    agent, recovery = owner
    recovery.start_step(agent, 'one', 'terminal', {'command': 'send'})
    if dimension == 'home':
        monkeypatch.setenv('STARDUST_HOME', f'foreign-{attempt}')
    elif dimension == 'profile':
        monkeypatch.setenv('STARDUST_HOME', str(Path.home() / 'profiles' / f'foreign-{attempt}'))
    elif dimension == 'identity':
        agent.identity['individual_id'] = f'foreign-{attempt}'
    requested_task = 'foreign' if dimension == 'task' else 'task-a'
    recovery.prepare(agent, 'continue', task_id=requested_task)
    result = recovery.find_resume_point(agent, task_id=requested_task)
    assert result['status'] == 'rejected'
    assert recovery.before_dispatch(agent, 'two', 'terminal', {'command': 'send'})['status'] in {'rejected', 'needs_review'}


@pytest.mark.parametrize('bad', [None, {}, {'version': 999}, {'version': True}, 'broken'])
def test_corrupt_checkpoint_rejected(owner, bad):
    agent, recovery = owner
    agent._session_db.patch_session_model_config(agent.session_id, {recovery.KEY: bad})
    assert recovery.find_resume_point(agent, task_id='task-a')['status'] == 'rejected'
    assert recovery.before_dispatch(agent, 'one', 'terminal', {})['status'] == 'rejected'


def test_committed_effect_replayed_without_dispatch(owner):
    agent, recovery = owner
    args = {'command': 'send'}
    recovery.start_step(agent, 'one', 'terminal', args)
    recovery.commit_step(agent, 'one', 'sent')
    recovery.prepare(agent, 'continue', task_id='task-a')
    assert recovery.before_dispatch(agent, 'two', 'terminal', args) == {'status': 'replay', 'result': 'sent'}
    assert recovery.before_dispatch(agent, 'three', 'terminal', args)['status'] == 'needs_review'


def test_flag_off_writes_nothing(owner, monkeypatch):
    agent, recovery = owner
    monkeypatch.setattr(recovery, 'enabled', lambda: False)
    before = agent._session_db.get_session_model_config_value(agent.session_id, recovery.KEY)
    assert recovery.prepare(agent, 'new goal') == ''
    assert recovery.before_dispatch(agent, 'two', 'terminal', {}) is None
    recovery.start_step(agent, 'two', 'terminal', {})
    recovery.commit_step(agent, 'two', 'done')
    assert agent._session_db.get_session_model_config_value(agent.session_id, recovery.KEY) == before


def test_success_receipt_closes_post_transcript_crash_window(owner):
    agent, recovery = owner
    recovery.start_step(agent, 'one', 'terminal', {'command': 'send'})
    agent._session_db.append_message('recovery', role='tool', content='sent', tool_call_id='one',
                                    tool_name='terminal', display_metadata={
                                        'task_recovery': {'version': 1, 'committed': True}})
    point = recovery.find_resume_point(agent, task_id='task-a')
    assert point['status'] == 'ready'
    assert point['last_committed_step'] == 1


def test_unmarked_or_unknown_result_cannot_commit_effect(owner):
    agent, recovery = owner
    recovery.start_step(agent, 'one', 'terminal', {'command': 'send'})
    agent._session_db.append_message('recovery', role='tool', content='possibly sent',
                                    tool_call_id='one', tool_name='terminal', effect_disposition='unknown')
    assert recovery.find_resume_point(agent, task_id='task-a')['status'] == 'needs_review'


def test_pending_side_effect_is_not_a_success_receipt(owner):
    _, recovery = owner
    assert not recovery.result_committed('terminal', '{"status":"running","exit_code":null}')
    assert not recovery.result_committed('terminal', '{"status":"unknown","success":true}')
    assert recovery.result_committed('terminal', '{"exit_code":0}')
    assert recovery.result_committed('write_file', '{"bytes_written":6,"verified":true}')


def test_normal_parallel_starts_do_not_enter_recovery(owner):
    agent, recovery = owner
    recovery.start_step(agent, 'one', 'terminal', {'command': 'one'})
    assert recovery.before_dispatch(agent, 'two', 'terminal', {'command': 'two'}) is None
    recovery.start_step(agent, 'two', 'terminal', {'command': 'two'})
    recovery.commit_step(agent, 'two', 'two done')
    recovery.commit_step(agent, 'one', 'one done')
    assert recovery.find_resume_point(agent, task_id='task-a')['last_committed_step'] == 2
