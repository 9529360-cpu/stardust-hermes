"""Paired admission and inference transport contracts (no real model)."""
from evals.digital_life import understand_user_paired as paired


def test_pair_reservation_interleaves_and_stops_before_partial_pair():
    order = []
    def run(case, arm, reservation):
        order.append((case['id'], arm, reservation))
        return {'id': case['id'], 'passed': True}
    result = paired.run_pairs([{'id': str(i)} for i in range(3)], run, cap=13)
    assert order == [('0', 'off', 3), ('0', 'on', 3), ('1', 'off', 3), ('1', 'on', 3)]
    assert result['reserved'] == 12
    assert len(result['pairs']) == 2
    assert result['stop_reason'] == 'budget'


def test_failed_on_discards_off_from_paired_scores():
    def run(case, arm, reservation):
        if arm == 'on':
            raise RuntimeError('transport stopped')
        return {'id': case['id']}
    result = paired.run_pairs([{'id': 'u01'}], run, cap=6)
    assert result['pairs'] == []
    assert result['stop_reason'] == 'RuntimeError'
    assert result['incomplete']['id'] == 'u01'


def test_aux_cost_and_candidate_exclusion():
    assert paired.case_cost({'agent': {'turn_intent_llm': True}}) == 4
    off = paired.flag_config('off', 'all')
    on = paired.flag_config('on', 'all')
    assert on['tool_loop_guardrails']['identical_failure_cap'] is True
    assert on['tool_loop_guardrails']['identical_failure_cap_shadow'] is False
    assert off['tool_loop_guardrails']['identical_failure_cap'] is False
    candidates = paired.flag_config('on', 'candidate')
    assert 'turn_intent_llm' not in candidates.get('agent', {})
    assert candidates['terminal']['cwd_env_policy'] == 'session_first'


def test_fixture_contract_keeps_correct_read_action_scoreable():
    from types import SimpleNamespace
    from evals.digital_life import understand_user as uu
    agent = SimpleNamespace(tools=[{'function': {'name': n}} for n in
                                   ('terminal', 'read_file', 'tool_search', 'clarify')])
    text = paired.bind_fixture(agent, {'files': {'envs/runtime/service.log': 'running'}}, 'inspect')
    assert [t['function']['name'] for t in agent.tools] == ['read_file']
    assert 'envs/runtime/service.log' in text
    assert 'inspect' in text
    assert agent._handle_max_iterations([], 2) == ''
    assert uu.MAX_AGENT_API_CALLS == 2
    agent.tools = [{'function': {'name': n}} for n in ('read_file', 'patch', 'write_file')]
    paired.bind_fixture(agent, {'files': {}}, 'fix it')
    assert agent.valid_tool_names == {'read_file'}
    agent.tools = [{'function': {'name': 'write_file'}}]
    paired.bind_fixture(agent, {'allowed_tools': ['write_file']}, 'write it')
    assert agent.valid_tool_names == {'write_file'}


def test_wire_budget_includes_aux_and_metadata_is_free():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    import pytest

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

        do_GET = do_POST = reply

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with paired.CountingRelay(12, upstream_port=upstream.server_port) as relay:
            url = f'http://127.0.0.1:{relay.server.server_port}'
            with urlopen(url + '/v1/models') as response:
                assert response.status == 200
            order = []
            def run(case, arm, reservation):
                relay.allowance, relay.arm_calls = reservation, 0
                order.append((case['id'], arm))
                # main action, final response, judge all use the same transport.
                for path in ('/v1/chat/completions', '/v1/responses', '/v1/messages'):
                    with urlopen(Request(url + path, data=b'{}')) as response:
                        assert response.status == 200
                return {'id': case['id']}
            result = paired.run_pairs([{'id': str(i)} for i in range(3)], run, cap=12)
            assert order == [('0', 'off'), ('0', 'on'), ('1', 'off'), ('1', 'on')]
            assert len(result['pairs']) == 2
            assert relay.forwarded == result['reserved'] == 12
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url + '/v1/messages', data=b'{}'))
            assert error.value.code == 400
            assert relay.forwarded == 12
            assert 'authorization' not in json.dumps(relay.calls).lower()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def test_only_inference_posts_count():
    assert paired.is_inference('POST', '/v1/chat/completions')
    assert paired.is_inference('POST', '/v1/responses?x=1')
    assert paired.is_inference('POST', '/v1/messages')
    assert not paired.is_inference('GET', '/v1/models')
    assert not paired.is_inference('POST', '/other')
