"""Provenance labels are advisory and only committed executor observations count."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent import task_acceptance as acceptance
from agent import acceptance_observations as observations


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path / 'home'))
    (tmp_path / 'home').mkdir()
    (tmp_path / 'home' / 'config.yaml').write_text('agent:\n  bind_acceptance_observations: true\n')
    (tmp_path / 'app').mkdir()
    contract = acceptance.begin('s', 'Acceptance criteria:\n- [runtime] Save result', tmp_path / 'app')
    artifact = tmp_path / 'saved.txt'
    artifact.write_text('saved')
    return contract, artifact


def commit(contract, artifact, *, name='read_file', call='call1', task='task', result=None):
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        if not db.get_session('s'):
            db.create_session(session_id='s', source='cli')
        rows = [dict(role='tool', content='actual result', tool_name=name, tool_call_id=call)]
        db.append_messages_batch('s', rows)
        agent = SimpleNamespace(session_id='s', _session_db=db)
        observations.record(agent, dict(_row_id=rows[0]['_row_id']), name,
                            {'path': str(artifact)}, result or {'path': str(artifact), 'content': 'saved'}, call, task)
    finally:
        db.close()


def test_committed_file_identity_and_unknown_sources(context):
    contract, artifact = context
    commit(contract, artifact)
    receipt = observations.resolve(contract, 'call1', artifact, 'runtime')
    assert receipt['verification'] == 'verified'
    assert receipt['observation_id'].startswith('message:')
    assert receipt['timestamp'] and receipt['tier'] == 'runtime'
    assert receipt['artifact_sha256'] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert receipt['action'] == {'tool': 'read_file', 'tool_call_id': 'call1', 'task_id': 'task'}
    for source in ['forged', '', 'message:999999']:
        unresolved = observations.resolve(contract, source, artifact, 'runtime')
        assert unresolved['verification'] == 'self-reported/unverified'
        assert unresolved['reason']
    artifact.write_text('changed')
    assert observations.resolve(contract, 'call1', artifact, 'runtime')['verification'] != 'verified'


def test_screenshot_requires_earlier_ui_action_in_same_task(context):
    contract, artifact = context
    commit(contract, artifact, name='browser_screenshot', call='shot')
    assert observations.resolve(contract, 'shot', artifact, 'user-flow')['verification'] != 'verified'
    commit(contract, artifact, name='browser_click', call='click', task='other')
    assert observations.resolve(contract, 'shot', artifact, 'user-flow')['verification'] != 'verified'
    commit(contract, artifact, name='browser_click', call='click2')
    assert observations.resolve(contract, 'shot', artifact, 'user-flow')['verification'] != 'verified'
    commit(contract, artifact, name='browser_screenshot', call='reused-old-shot')
    assert observations.resolve(contract, 'reused-old-shot', artifact, 'user-flow')['verification'] != 'verified'
    artifact.write_text('fresh capture after action')
    commit(contract, artifact, name='browser_screenshot', call='shot2')
    assert observations.resolve(contract, 'shot2', artifact, 'user-flow')['verification'] == 'verified'


def test_fixture_only_and_different_contract_remain_unverified(context):
    contract, artifact = context
    assert observations.resolve(contract, 'fixture', artifact, 'runtime')['verification'] != 'verified'
    commit(contract, artifact)
    contract['created_at'] = '2999-01-01T00:00:00+00:00'
    assert observations.resolve(contract, 'call1', artifact, 'runtime')['verification'] != 'verified'


def test_uncommitted_row_and_metadata_failure_are_advisory(context, monkeypatch):
    import sqlite3
    contract, artifact = context
    agent = SimpleNamespace(session_id='s', _session_db=SimpleNamespace(db_path=artifact.parent / 'missing.db'))
    observations.record(agent, {'_row_id': 999}, 'read_file', {'path': str(artifact)}, {}, 'absent', 'task')
    assert observations.resolve(contract, 'absent', artifact, 'runtime')['verification'] != 'verified'
    monkeypatch.setattr(acceptance, 'load', lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    observations.record(agent, {'_row_id': 999}, 'read_file', {'path': str(artifact)}, {}, 'absent', 'task')


def test_terminal_fixture_path_is_not_promoted(context):
    contract, artifact = context
    commit(contract, artifact, name='terminal', result={'path': str(artifact), 'exit_code': 0})
    assert observations.resolve(contract, 'call1', artifact, 'runtime')['verification'] != 'verified'


def test_receipt_labels_do_not_change_acceptance_decisions(context, monkeypatch, capsys):
    contract, artifact = context
    contract['plan'] = ['save', 'read']
    acceptance.save(contract)
    commit(contract, artifact)
    for source, label in [('call1', 'verified'), ('never-recorded', 'self-reported/unverified')]:
        monkeypatch.setattr('sys.argv', ['acceptance', '--session', 's', 'evidence',
                            '--criterion', 'C1', '--tier', 'runtime', '--status', 'passed',
                            '--source', source, '--artifact', str(artifact)])
        acceptance.main()
        receipt = json.loads(capsys.readouterr().out)
        assert receipt['contract']['evidence'][-1]['provenance']['verification'] == label
        assert receipt['unmet'] == []


def test_disabled_has_no_observation_side_effects(context):
    contract, artifact = context
    (artifact.parent / 'home' / 'config.yaml').write_text('agent:\n  bind_acceptance_observations: false\n')
    assert observations.enabled() is False
    commit(contract, artifact)
    from agent.verification_evidence import _transaction
    with _transaction() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='acceptance_observations'").fetchone()
