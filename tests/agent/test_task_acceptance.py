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


@pytest.mark.parametrize('prompt,expected', [
    ('Create result.txt containing 42 and read it back.', ('result.txt', '42', True)),
    ('请生成 result.txt，内容为 42，并读回核对。', ('result.txt', '42', True)),
    ('Write "a b.txt" containing "hello world"', ('a b.txt', 'hello world', False)),
    ('How do I create result.txt containing 42?', None),
    ('Do not create result.txt', None),
    ('Explain this code', None),
])
def test_artifact_request_contract(workspace, prompt, expected):
    contract = acceptance.artifact_contract(prompt, workspace)
    if expected is None:
        assert contract is None
    else:
        assert contract == dict(path=str((workspace / expected[0]).resolve()),
                                content=expected[1], read_back=expected[2])


def test_artifact_requires_current_successful_write_and_read(workspace):
    path = workspace / 'result.txt'
    path.write_text('42', encoding='utf8')
    agent = SimpleNamespace(_artifact_contract=acceptance.artifact_contract(
        f'Create {path.as_posix()} containing 42 and read it back', workspace))
    assert acceptance.check_artifact(agent, [])['task_verified'] is False
    def pair(name, args, receipt, ident):
        return [dict(role='assistant', tool_calls=[dict(id=ident, function=dict(
            name=name, arguments=json.dumps(args)))]),
            dict(role='tool', tool_call_id=ident, content=json.dumps(receipt))]
    write = pair('write_file', dict(path=str(path), content='42'),
                 dict(files_modified=[str(path)]), 'w')
    read = pair('read_file', dict(path=str(path)), dict(content='1|42'), 'r')
    assert acceptance.check_artifact(agent, write)['task_verified'] is False
    assert acceptance.check_artifact(agent, write + read)['task_verified'] is True
    assert acceptance.check_artifact(agent, read + write)['task_verified'] is False
    assert acceptance.check_artifact(agent, write + read + write)['task_verified'] is False
    path.write_text('wrong', encoding='utf8')
    assert acceptance.check_artifact(agent, write + read)['task_verified'] is False
    path.write_text('42', encoding='utf8')
    assert acceptance.check_artifact(agent, write + read + [dict(role='user',content='new turn')])['task_verified'] is False
    failure = pair('write_file', dict(path=str(path)),
                   dict(error='Permission denied', files_modified=[str(path)]), 'f')
    assert acceptance.check_artifact(agent, failure + read)['task_verified'] is False
    assert acceptance.check_artifact(agent, write[:1] + read)['task_verified'] is False
    path.unlink()
    assert acceptance.check_artifact(agent, write + read)['task_verified'] is False


@pytest.mark.parametrize('value', [None, False, 'false', 'true', 1, True])
def test_artifact_flag_is_explicit_opt_in(monkeypatch, value):
    monkeypatch.setattr('hermes_cli.config.load_config_readonly',
                        lambda: {'agent': {'artifact_acceptance': value}})
    assert acceptance.artifact_acceptance_enabled() is (value is True)
