"""F5.2: deterministic loopback model through the real agent turn/finalizer."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ZH = ["我来处理。", "好的，我现在就去改。", "我会修复这个问题。", "我马上检查文件。", "我这就运行测试。", "我来查看日志。", "我现在开始排查。", "好的，我会检查仓库。", "我将修改配置。", "我先读取文件。"]
EN = ["I'll fix that now.", "I will check the files.", "Let me inspect the repository.", "I'll run the tests.", "I will debug this issue.", "Let me review the logs.", "I'll search the codebase.", "I will open the file.", "Let me read the configuration.", "I'll look into the error."]
CONTROLS = [
    ("解释一下这个错误。", "我会先解释原因：这是类型不匹配造成的。"),
    ("给我一个计划。", "我会先检查文件，再运行测试，最后修复问题。"),
    ("你会怎么做？", "我会先查看日志。"),
    ("只讨论，不做事，解释这个问题。", "我来解释：返回值为空。"),
    ("这是什么意思？", "意思是文件不存在。"),
    ("What would you do?", "I'll inspect the repository first."),
    ("Give me a plan.", "I will check the files, then run tests."),
    ("Explain this error.", "Let me explain: the file is missing."),
    ("How would you fix this?", "I'll fix the type mismatch by checking the input."),
    ("Can you describe the approach without executing?", "I will review the logs first."),
]


@pytest.fixture
def loopback():
    state = {"reply": "", "calls": 0, "tool_calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/chat/completions'):
                state['calls'] += 1
                state['tool_calls'] = sum(len(m.get('tool_calls', [])) for m in body['messages'])
            payload = {'id': 'local', 'object': 'chat.completion', 'created': 1,
                       'model': 'local-scripted', 'choices': [{'index': 0, 'message': {
                           'role': 'assistant', 'content': state['reply']}, 'finish_reason': 'stop'}],
                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            if body.get('stream'):
                payload['object'] = 'chat.completion.chunk'
                payload['choices'][0]['delta'] = payload['choices'][0].pop('message')
                data = ('data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n').encode()
                content_type = 'text/event-stream'
            else:
                data = json.dumps(payload).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f'http://127.0.0.1:{server.server_port}/v1'
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def run_case(loopback, request, reply):
    from run_agent import AIAgent
    state, url = loopback
    state.update(reply=reply, calls=0, tool_calls=0)
    agent = AIAgent(model='local-scripted', api_key='local-test', base_url=url,
                    provider='custom', api_mode='chat_completions', max_iterations=8,
                    enabled_toolsets=['terminal'], quiet_mode=True, skip_memory=True,
                    skip_context_files=True, save_trajectories=False)
    agent._intent_ack_continuation = True
    result = agent.run_conversation(request)
    return {'request': request, 'reply': reply, 'nudged': state['calls'] > 1,
            'calls': state['calls'], 'tool_calls': state['tool_calls'],
            **{key: result.get(key) for key in ('completed', 'failed', 'final_response', 'turn_exit_reason')}}


def test_loopback_matrix(loopback, monkeypatch):
    mode = os.environ.get('F52_MATRIX', 'on')
    monkeypatch.setattr('hermes_cli.config.load_config',
                        lambda: {'agent': {'commitment_stall_zh': mode == 'on'}})
    rows = []
    for language, replies in [('zh', ZH), ('en', EN)]:
        for reply in replies:
            row = run_case(loopback, '请修复当前仓库的错误。 Fix the error in this repository.', reply)
            row['group'] = language
            rows.append(row)
    for request, reply in CONTROLS:
        rows.append({'group': 'control', **run_case(loopback, request, reply)})
    evidence = os.environ.get('F52_EVIDENCE')
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        Path(evidence, mode + '.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    if mode == 'baseline':
        assert sum(r['nudged'] for r in rows[:10]) == 0
        assert sum(r['nudged'] for r in rows[10:20]) == 10
        assert all(r['completed'] for r in rows[:20])
    elif mode == 'off':
        baseline = json.loads(Path(evidence, 'baseline.json').read_text(encoding='utf-8'))
        assert rows == baseline
    else:
        for row in rows[:20]:
            assert row['nudged'] and row['calls'] == 3, row
            assert row['completed'] is False, row
            assert 'Blocked / 未完成' in row['final_response'], row
        assert all(not r['nudged'] and r['completed'] for r in rows[20:])
    assert all(r['tool_calls'] == 0 for r in rows)


def test_detector_boundaries_and_flag_default(monkeypatch):
    from types import SimpleNamespace
    from agent.agent_runtime_helpers import commitment_stall_zh_enabled, pure_commitment_stall
    agent = SimpleNamespace(_strip_think_blocks=lambda text: text)
    request = '修复错误'
    history = [{'role': 'user', 'content': request}, {'role': 'tool', 'content': 'old'},
               {'role': 'user', 'content': request}]
    assert pure_commitment_stall(agent, request, '我来处理。', history)
    assert not pure_commitment_stall(agent, request, '我来处理。',
                                     history + [{'role': 'tool', 'content': 'new'}])
    for text in ('我来处理，已经完成。', '我来处理。原因是类型错误。',
                 "I'll fix it. The cause is a missing file.", '已经修复。'):
        assert not pure_commitment_stall(agent, request, text, history)
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {})
    assert commitment_stall_zh_enabled(agent) is False
    for value in (False, 'false', 'true', 1):
        monkeypatch.setattr('hermes_cli.config.load_config',
                            lambda: {'agent': {'commitment_stall_zh': value}})
        assert commitment_stall_zh_enabled(SimpleNamespace()) is False
