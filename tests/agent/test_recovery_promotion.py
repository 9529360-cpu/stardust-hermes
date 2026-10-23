"""Recovery promotion requires committed, scoped evidence, never a model claim."""
import json
import time
from types import SimpleNamespace

import pytest

from agent import recovery_promotion as promotion


def test_disabled_is_read_and_write_free():
    agent = SimpleNamespace()
    assert promotion.build_brief(agent, {}, []) == ''
    assert promotion.build_brief(agent, {'promote_verified_recoveries': False}, []) == ''


def test_expired_retracted_and_duplicate_hint():
    block = promotion.hint_block('file-search', 'session-1', [2, 4, 6], time.time())
    assert len(block) <= 600
    assert promotion.render_hints(block, enabled=True) == block
    assert promotion.render_hints(block, enabled=True, now=time.time() + 90000) == ''
    assert promotion.render_hints(block.replace('"active":true', '"active":false'), enabled=True) == ''
    assert promotion.render_hints(block, enabled=False) == block
    assert promotion.hint_key('file-search') in block

@pytest.fixture
def scenario(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('STARDUST_HOME', str(home))
    (home / 'config.yaml').write_text('agent:\n  bind_acceptance_observations: true\n')
    from hermes_state import SessionDB
    from agent import task_acceptance, acceptance_observations
    db = SessionDB(db_path=home / 'state.db')
    db.create_session(session_id='recovery-session', source='cli')
    agent = SimpleNamespace(session_id='recovery-session', _session_db=db)
    contract = task_acceptance.begin(agent.session_id, 'Acceptance criteria:\n- [runtime] Find literal', tmp_path)
    artifact = tmp_path / 'sample.txt'
    artifact.write_text('needle\n')
    rows = []
    def commit(name, args, result):
        cid = f'call-{len(rows)}'
        batch = [dict(role='assistant', tool_calls=[dict(id=cid, function=dict(name=name, arguments=json.dumps(args)))]),
                 dict(role='tool', tool_name=name, tool_call_id=cid, content=json.dumps(result))]
        db.append_messages_batch(agent.session_id, batch)
        rows.extend(batch)
        return batch[-1], cid
    commit('skill_view', {'name': 'file-search'}, {'success': True})
    commit('terminal', {'command': 'rg needle sample.txt', 'workdir': str(tmp_path)},
           {'exit_code': 127, 'output': 'bash: rg: command not found\nPRIVATE_TOKEN=do-not-copy'})
    # A malformed diagnostic must be rejected; set a genuine anchored shell diagnostic below.
    failure = rows[-1]
    commit('terminal', {'command': 'grep needle sample.txt', 'workdir': str(tmp_path)}, {'exit_code': 0, 'output': 'needle\n'})
    row, cid = commit('read_file', {'path': str(artifact)}, {'content': 'needle\n'})
    acceptance_observations.record(agent, row, 'read_file', {'path': str(artifact)}, {}, cid, 'task')
    contract['evidence'] = [dict(criterion='C1', tier='runtime', status='passed', source=cid,
        artifact=str(artifact), provenance=acceptance_observations.resolve(contract, cid, artifact, 'runtime'))]
    task_acceptance.save(contract)
    yield agent, rows, contract, artifact, failure
    db.close()


def test_claim_or_private_diagnostic_not_promoted(scenario):
    agent, rows, contract, artifact, failure = scenario
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows) == ''


def test_unverified_and_other_home_fail_closed(scenario, monkeypatch, tmp_path):
    agent, rows, contract, artifact, failure = scenario
    import sqlite3
    from agent import task_acceptance
    with sqlite3.connect(agent._session_db.db_path) as conn:
        conn.execute('UPDATE messages SET content=? WHERE id=?',
                     (json.dumps({'exit_code': 127, 'output': 'bash: rg: command not found'}), failure['_row_id']))
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows)
    block = promotion.hint_block('file-search', agent.session_id, [4, 6, 8], time.time())
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(other))
    monkeypatch.setenv('STARDUST_HOME', str(other))
    assert promotion.render_hints(block, enabled=True) == ''
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows) == ''
    monkeypatch.setenv('HERMES_HOME', str(artifact.parent / 'home'))
    monkeypatch.setenv('STARDUST_HOME', str(artifact.parent / 'home'))
    contract['evidence'][0]['source'] = 'model-claim'
    task_acceptance.save(contract)
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows) == ''


def test_committed_chain_and_artifact_required(scenario):
    agent, rows, contract, artifact, failure = scenario
    # Correct the persisted diagnostic, not just a model-visible projection.
    import sqlite3
    with sqlite3.connect(agent._session_db.db_path) as conn:
        conn.execute('UPDATE messages SET content=? WHERE id=?',
                     (json.dumps({'exit_code': 127, 'output': 'bash: rg: command not found'}), failure['_row_id']))
    brief = promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows)
    assert 'file-search' in brief and 'recovery-session' in brief
    assert all(f'message:{r["_row_id"]}' in brief for r in [rows[3], rows[5], rows[7]])
    assert 'PRIVATE_TOKEN' not in brief and str(artifact) not in brief
    artifact.write_text('changed')
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows) == ''
    # A new user turn must not recycle an earlier recovery into unrelated work.
    agent._session_db.append_messages_batch(agent.session_id, [dict(role='user', content='unrelated task')])
    assert promotion.build_brief(agent, {'promote_verified_recoveries': True}, rows) == ''
