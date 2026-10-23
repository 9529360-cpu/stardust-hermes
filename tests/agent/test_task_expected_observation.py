"""Real boundaries for opt-in expected observations (no external services)."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from agent import task_acceptance as acceptance


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'workspace'
    root.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('STARDUST_HOME', str(home))
    acceptance.begin('s', 'Acceptance criteria:\n- [runtime] Observe result', root)
    return root, tmp_path


def cli(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['task_acceptance', '--session', 's', *args])
    acceptance.main()
    return acceptance.load('s')


def plan(monkeypatch, artifact, expected, observer='json'):
    return cli(monkeypatch, 'plan', '--data', json.dumps({'steps': [{
        'criterion': 'C1', 'action_category': 'observe', 'expected_result': expected,
        'observer_type': observer, 'evidence_ref': str(artifact),
    }]}))


def record(monkeypatch, artifact, status='passed'):
    return cli(monkeypatch, 'evidence', '--criterion', 'C1', '--tier', 'runtime',
               '--status', status, '--source', 'real local observation', '--artifact', str(artifact))


def test_real_file_write_readback(scenario, monkeypatch):
    root, temp = scenario
    artifact = temp / 'readback.txt'
    plan(monkeypatch, artifact, 'hello 星尘', 'text')
    target = root / 'result.txt'
    target.write_text('hello 星尘', encoding='utf-8')
    artifact.write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
    contract = record(monkeypatch, artifact)
    assert contract['evidence'][-1]['observations'][0]['comparison'] == 'matched'
    assert acceptance.unmet(contract) == []


def test_real_http_unexpected_status(scenario, monkeypatch):
    _, temp = scenario
    artifact = temp / 'http.json'
    plan(monkeypatch, artifact, 200)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f'http://127.0.0.1:{server.server_port}', timeout=3)
        artifact.write_text(json.dumps({'observed': error.value.code}), encoding='utf-8')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    contract = record(monkeypatch, artifact)
    assert contract['evidence'][-1]['observations'][0]['comparison'] == 'mismatched'
    assert acceptance.unmet(contract)


def test_real_interrupted_process(scenario, monkeypatch):
    _, temp = scenario
    artifact = temp / 'process.json'
    ready = temp / 'ready'
    plan(monkeypatch, artifact, 0)
    child = subprocess.Popen([sys.executable, '-c',
        'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text("started"); time.sleep(30)', str(ready)])
    try:
        import time
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        child.kill()
        child.wait(timeout=5)
        artifact.write_text(json.dumps({'interrupted': True}), encoding='utf-8')
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    contract = record(monkeypatch, artifact)
    assert contract['evidence'][-1]['observations'][0]['comparison'] == 'unknown'
    assert acceptance.unmet(contract)


@pytest.mark.parametrize('status,satisfied', [('passed', True), ('failed', False)])
def test_legacy_cli_unchanged(scenario, monkeypatch, status, satisfied):
    _, temp = scenario
    cli(monkeypatch, 'plan', '--data', '{"steps":["observe"]}')
    artifact = temp / 'legacy.txt'
    artifact.write_text('legacy', encoding='utf-8')
    contract = record(monkeypatch, artifact, status)
    assert 'observations' not in contract['evidence'][-1]
    assert (acceptance.unmet(contract) == []) is satisfied


@pytest.mark.parametrize('observer,content,expected,comparison', [
    ('json', '{"observed":false}', 0, 'mismatched'),
    ('json', '{"observed":null}', None, 'matched'),
    ('json', '{"observed":0,"interrupted":true}', 0, 'unknown'),
    ('json', 'invalid json', 0, 'unknown'),
    ('visual', 'ok', 'ok', 'unknown'),
    ('text', 'ok\n', 'ok', 'mismatched'),
])
def test_strict_comparison(scenario, monkeypatch, observer, content, expected, comparison):
    _, temp = scenario
    artifact = temp / 'observation'
    plan(monkeypatch, artifact, expected, observer)
    artifact.write_text(content, encoding='utf-8')
    contract = record(monkeypatch, artifact)
    assert contract['evidence'][-1]['observations'][0]['comparison'] == comparison
    assert (acceptance.unmet(contract) == []) is (comparison == 'matched')


def test_changed_expectation_and_legacy_evidence_require_new_observation(scenario, monkeypatch):
    _, temp = scenario
    artifact = temp / 'result'
    cli(monkeypatch, 'plan', '--data', '{"steps":["legacy"]}')
    artifact.write_text('ok', encoding='utf-8')
    assert acceptance.unmet(record(monkeypatch, artifact)) == []
    assert acceptance.unmet(plan(monkeypatch, artifact, 'ok', 'text'))
    assert acceptance.unmet(record(monkeypatch, artifact)) == []
    assert acceptance.unmet(plan(monkeypatch, artifact, 'different', 'text'))
    assert acceptance.unmet(record(monkeypatch, artifact))


def test_wrong_reference_and_tampered_artifact_cannot_satisfy(scenario, monkeypatch):
    _, temp = scenario
    artifact = temp / 'actual'
    plan(monkeypatch, temp / 'expected', 'ok', 'text')
    artifact.write_text('ok', encoding='utf-8')
    contract = record(monkeypatch, artifact)
    assert contract['evidence'][-1]['observations'][0]['comparison'] == 'unknown'
    assert acceptance.unmet(contract)
    plan(monkeypatch, artifact, 'ok', 'text')
    contract = record(monkeypatch, artifact)
    assert acceptance.unmet(contract) == []
    artifact.write_text('changed', encoding='utf-8')
    assert acceptance.unmet(contract)


def test_two_steps_keep_separate_observations(scenario, monkeypatch):
    _, temp = scenario
    a, b = temp / 'a', temp / 'b'
    steps = [{'criterion': 'C1', 'expected_result': 'ok', 'observer_type': 'text',
              'evidence_ref': str(path)} for path in (a, b)]
    cli(monkeypatch, 'plan', '--data', json.dumps({'steps': steps}))
    for path in (a, b):
        path.write_text('ok', encoding='utf-8')
        contract = record(monkeypatch, path)
    assert acceptance.unmet(contract) == []
    a.write_text('bad', encoding='utf-8')
    assert acceptance.unmet(record(monkeypatch, a))


def test_expected_plan_obeys_user_steering(scenario, monkeypatch):
    from types import SimpleNamespace
    from agent.prompt_builder import steer_user_row
    _, temp = scenario
    contract = plan(monkeypatch, temp / 'result', 'ok', 'text')
    agent = SimpleNamespace(session_id='s', _task_acceptance_active=True,
                            _task_acceptance_request=contract['goal'])
    acceptance.refresh_current_goal(agent, [steer_user_row('Stop and explain the design')])
    assert acceptance.load('s')['active'] is False
    assert acceptance.finalize(agent, 'Explanation') == 'Explanation'
    replacement = acceptance.begin('s', 'Acceptance criteria:\n- [review] Read design', scenario[0])
    assert replacement['plan'] is None
    assert replacement['evidence'] == []
