import json
from types import SimpleNamespace

import pytest

from agent import task_acceptance as acceptance


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'app'
    root.mkdir()
    (root / 'index.html').write_text('<button>Go</button>', encoding='utf-8')
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('STARDUST_HOME', str(home))
    return root


def test_opt_in_current_goal_and_resume(workspace):
    assert acceptance.begin('s', 'Hello', workspace) is None
    contract = acceptance.begin('s', '[complex] Fix button\nAcceptance criteria:\n- Clicking button shows result', workspace)
    assert contract['criteria'][0]['tier'] == 'user-flow'
    contract['plan'] = ['inspect', 'fix', 'click']
    acceptance.save(contract)
    assert acceptance.begin('s', 'continue', workspace)['plan'] == contract['plan']
    acceptance.carry('s', 'child')
    assert acceptance.load('child')['goal'] == contract['goal']
    assert acceptance.begin('s', 'Explain a word', workspace) is None
    assert acceptance.begin('s', 'continue', workspace)['plan'] == contract['plan']
    replacement = acceptance.begin('s', 'Acceptance criteria:\n- Review README.md', workspace)
    assert replacement['criteria'][0]['tier'] == 'review'
    assert replacement['evidence'] == []


def test_test_pass_cannot_release_ui_failed_flow_and_edits_invalidate(workspace):
    contract = acceptance.begin('s', '[complex] Fix button\nAcceptance criteria:\n- Click button shows result', workspace)
    contract['plan'] = ['fix', 'click']
    current = acceptance.change_identity(str(workspace))
    contract['evidence'] = [dict(criterion='C1', tier='test', status='passed', change=current)]
    assert acceptance.unmet(contract)
    contract['evidence'].append(dict(criterion='C1', tier='user-flow', status='failed', change=current))
    acceptance.save(contract)
    agent = SimpleNamespace(session_id='s', _task_acceptance_active=True)
    text = acceptance.finalize(agent, 'Done, all tests passed.')
    assert 'NOT COMPLETE' in text and 'C1:' in text and 'failed' in text
    contract['evidence'].append(dict(criterion='C1', tier='user-flow', status='passed', change=current))
    assert acceptance.unmet(contract) == []
    (workspace / 'index.html').write_text('changed', encoding='utf-8')
    assert 'no current evidence' in acceptance.unmet(contract)[0]


def test_budget_summary_user_row_cannot_disable_contract(workspace):
    from agent.prompt_builder import steer_user_row
    contract = acceptance.begin("s", "Acceptance criteria:\n- Click button shows result", workspace)
    agent = SimpleNamespace(session_id="s", _task_acceptance_active=True,
                            _task_acceptance_request=contract["goal"])
    acceptance.refresh_current_goal(agent, [{"role": "user", "content": "You have reached the maximum iterations"}])
    assert agent._task_acceptance_active is True
    assert "NOT COMPLETE" in acceptance.finalize(agent, "Done")
    acceptance.refresh_current_goal(agent, [steer_user_row("Acceptance criteria:\n- Review README.md")])
    assert acceptance.load("s")["criteria"][0]["tier"] == "review"


def test_markdown_plan_and_review_needs_no_browser(workspace):
    contract = acceptance.begin('s', 'Acceptance criteria:\n- Review README.md for correct title and explain UI success', workspace)
    assert contract['criteria'][0]['tier'] == 'review'
    contract['plan'] = ['write', 'read back']
    contract['evidence'] = [dict(criterion='C1', tier='review', status='passed',
                                 change=acceptance.change_identity(str(workspace)))]
    assert acceptance.unmet(contract) == []


def test_real_command_records_source_time_and_change(workspace, monkeypatch, capsys):
    acceptance.begin('s', 'Acceptance criteria:\n- Click button shows result', workspace)
    monkeypatch.setattr('sys.argv', ['task_acceptance', '--session', 's', 'plan', '--data', json.dumps({'steps': ['click']})])
    acceptance.main()
    artifact = workspace.parent / 'observed.json'
    artifact.write_text('{"result":"empty"}', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['task_acceptance', '--session', 's', 'evidence', '--criterion', 'C1',
                                    '--tier', 'user-flow', '--status', 'failed', '--source', 'browser click #1',
                                    '--artifact', str(artifact)])
    acceptance.main()
    recorded = acceptance.load('s')['evidence'][0]
    assert recorded['change'] and recorded['created_at'] and recorded['artifact_sha256']
    assert recorded['source'] == 'browser click #1'
    assert 'failed' in capsys.readouterr().out
