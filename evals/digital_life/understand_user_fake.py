"""Deterministic loopback fake endpoint for the understand-user eval (offline mode).

Reuses the DL-P2.6 / assistant_scenarios FakeModel server (dynamic port 0).
The scripted agent never needs a real model: it answers with a restatement
assembled deterministically from the committed case fields, so the harness
mechanics (workspace seeding, trace extraction, scoring, persona check,
--ab plumbing) are exercised end to end with zero model calls.

The script is deliberately NOT an intelligent agent — it is a fixed
mechanics probe: match the committed user turn anywhere in the request
text (the production memory sidecar injects an extra user message, so
``last_user()`` is not reliably the case turn), perform the case's first
expected tool call once, then restate the case constraints.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from evals.assistant_scenarios.fake_model import FakeModel, Reply

_CASES_PATH = Path(__file__).resolve().parent / 'understand_user_cases.json'


@lru_cache(maxsize=1)
def _cases():
    return json.loads(_CASES_PATH.read_text(encoding='utf-8'))['cases']


def _match_case(request):
    """Match the committed user turn anywhere in the full request text."""
    text = request.text()
    for case in _cases():
        turn = case.get('user_turn') or ''
        if turn and turn in text:
            return case
    return None


def _restatement(case) -> str:
    groups = case.get('expected_constraints') or []
    parts = [g[0] if isinstance(g, list) else str(g) for g in groups]
    return '明白：' + '；'.join(parts) + '。' if parts else '明白。'


def _first_action_reply(case):
    first = (case.get('expected_first_action') or ['none'])[0]
    if first == 'none':
        return _restatement(case) + ' 按你的要求直接回应，不调用工具。'
    verb = {'read_file': '已读取', 'write_file': '已写入', 'search_files': '已检索'}.get(first, '已处理')
    arg = case.get('first_action_arg') or case.get('primary_file') or 'target'
    return f'{verb} {arg}。' + _restatement(case)


def _tool_args(first: str, arg: str) -> dict:
    if first == 'search_files':
        return {'pattern': arg, 'target': 'files'}
    if first == 'write_file':
        return {'path': arg, 'content': 'ok'}
    return {'path': arg}


def script(request):
    case = _match_case(request)
    if case is None:
        return Reply(text='ok')
    first = (case.get('expected_first_action') or ['none'])[0]
    if first != 'none' and not request.called(first):
        arg = case.get('first_action_arg') or case.get('primary_file') or 'target.txt'
        return Reply(tool_calls=[(first, _tool_args(first, arg))])
    return Reply(text=_first_action_reply(case))


def make_fake_endpoint() -> FakeModel:
    return FakeModel(script, model='understand-eval-local')
