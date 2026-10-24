"""Targeted tests for the DL-P2.8c understand-user eval (eval-only code).

Covers: fixed case-set invariants (counts, per-class minimums, counter-cases),
deterministic scoring of all four dimensions, persona sha256 guard, --ab flag
plumbing into the isolated HOME only, home-isolation guard, and the
zero-product-change contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVALS = REPO / 'evals' / 'digital_life'
CASES = EVALS / 'understand_user_cases.json'


def _load_cases():
    with CASES.open(encoding='utf-8') as f:
        return json.load(f)


def test_case_set_shape_is_fixed():
    data = _load_cases()
    meta = data['meta']
    assert meta['cases'] == 30
    assert meta['adapted_from_failures'] == 20
    assert meta['constructed'] == 10
    ids = [c['id'] for c in data['cases']]
    assert len(set(ids)) == 30
    adapted = [c for c in data['cases'] if c['origin'].startswith('failures.md')]
    assert [c['id'] for c in adapted] == [f'u{i:02d}' for i in range(1, 21)]
    # every case declares all scoring inputs
    for c in data['cases']:
        assert c['expected_constraints'], c['id']
        assert c['expected_first_action'], c['id']
        assert isinstance(c['evidence_required'], bool), c['id']
        assert isinstance(c['clarification_allowed'], bool), c['id']


def test_class_distribution_matches_plan():
    data = _load_cases()
    counts = {}
    for c in data['cases']:
        counts[c['class']] = counts.get(c['class'], 0) + 1
    # DISCOVERY-8 case-set plan (per-row labels of failures.md)
    assert counts['intent-not-restated'] == 10   # 8 adapted + 2 constructed
    assert counts['no-verification'] == 8        # 5 adapted + 2 (+1 mapped row 17)
    assert counts['tool-failure-hidden'] == 4     # 2 adapted + 2 constructed
    assert counts['stored-not-retrieved'] == 4    # 2 adapted + 2 constructed
    assert counts['outdated-contract'] == 3       # 1 adapted + 2 constructed
    assert counts['other'] == 1
    # constructed block: every root-cause class >= 2
    constructed_counts = {}
    for c in data['cases']:
        if c['origin'] == 'constructed':
            constructed_counts[c['class']] = constructed_counts.get(c['class'], 0) + 1
    assert all(v >= 2 for v in constructed_counts.values())


def test_counter_cases_present():
    data = _load_cases()
    by_id = {c['id']: c for c in data['cases']}
    # explicit user instruction overrides automatic behaviour (failures #17/#18)
    assert by_id['u17']['expected_constraints'][0][0] in ('不要重跑', '不要再重跑', '不重跑')
    assert 'none' in by_id['u18']['expected_first_action']
    # clear instruction must NOT trigger clarification: every committed case
    # is a clear-instruction case, so none of them warrants clarify
    assert all(c['clarification_allowed'] is False for c in data['cases'])


def test_no_real_user_data_in_cases():
    data = _load_cases()
    blob = json.dumps(data, ensure_ascii=False)
    for token in ('bz977', 'D:/Stardust/home', 'D:/项目/Stardust-Jarvis', '9529360'):
        assert token not in blob


def test_constraints_are_restatable_from_the_user_turn():
    """Dimension (a) scores by keyword surfacing, so every constraint group
    must have at least one alias that occurs verbatim in the user turn —
    otherwise the case demands an inference the scoring cannot see."""
    import re

    data = _load_cases()
    for c in data['cases']:
        for group in c['expected_constraints']:
            assert any(re.search(alias, c['user_turn'], re.IGNORECASE)
                       for alias in group), (c['id'], group)


sys.path.insert(0, str(REPO))

from evals.digital_life import understand_user as uu  # noqa: E402


def test_assert_home_rejects_unsafe_paths(tmp_path):
    safe = tmp_path / 'home'
    safe.mkdir()
    env = {k: str(safe) for k in uu.HOME_VARS}
    assert uu.assert_home(safe, env) == safe.resolve()
    with pytest.raises(ValueError):
        uu.assert_home(safe, {**env, 'TEMP': str(tmp_path / 'other')})
    with pytest.raises(ValueError):
        uu.assert_home(safe, {**env, 'HERMES_HOME': 'D:/Stardust/home'})


def test_persona_digests_detect_change(tmp_path):
    before = uu.persona_digests(tmp_path)
    assert set(before) == {'SOUL.md', 'AGENTS.md', 'USER.md'}
    assert all(v == '' for v in before.values())  # missing -> ''
    (tmp_path / 'SOUL.md').write_text('identity', encoding='utf-8')
    after = uu.persona_digests(tmp_path)
    assert after['SOUL.md'] != '' and after['USER.md'] == ''
    with pytest.raises(SystemExit, match='PERSONA MD CHANGED'):
        uu.check_persona_unchanged(before, after)
    uu.check_persona_unchanged(before, before)  # identical passes silently


def test_extract_trace_and_first_action():
    messages = [
        {'role': 'user', 'content': 'go'},
        {'role': 'assistant', 'tool_calls': [
            {'function': {'name': 'read_file', 'arguments': '{"path": "a.txt"}'}}]},
        {'role': 'tool', 'name': 'read_file', 'content': 'file body'},
        {'role': 'assistant', 'content': 'done'},
    ]
    trace = uu.extract_trace(messages)
    assert trace['first_tool'] == 'read_file'
    assert trace['calls'] == [('read_file', '{"path": "a.txt"}')]
    assert trace['results'] == [('read_file', 'file body')]
    assert uu.extract_trace([])['first_tool'] is None
    assert uu.extract_trace(None)['calls'] == []


def test_score_case_dimensions():
    case = {
        'id': 'x', 'class': 'intent-not-restated',
        'expected_constraints': [['9090'], ['直接']],
        'forbidden_additions': [['部署到服务器']],
        'expected_first_action': ['read_file'],
        'evidence_required': True,
        'clarification_allowed': False,
    }
    good_trace = {
        'first_tool': 'read_file',
        'calls': [('read_file', '{"path": "config.yaml"}')],
        'results': [('read_file', '{"success": true, "content": "port: 8080"}')],
    }
    row = uu.score_case(case, good_trace, '已把端口改为 9090，直接修改完成。')
    assert row['passed'] is True
    assert row['dimensions'] == {
        'intent_restated': True, 'first_action': True,
        'completion_evidence': True, 'no_needless_clarification': True}

    # wrong first action + needless clarify + unsupported completion claim
    bad_trace = {
        'first_tool': 'clarify',
        'calls': [('clarify', '{"question": "which port?"}')],
        'results': [],
    }
    row2 = uu.score_case(case, bad_trace, '已完成修改。')
    assert row2['passed'] is False
    assert row2['dimensions']['first_action'] is False
    assert row2['dimensions']['no_needless_clarification'] is False
    assert row2['dimensions']['completion_evidence'] is False

    # constraint found in tool args also counts (dimension-a alternative)
    trace_args = {
        'first_tool': 'read_file',
        'calls': [('read_file', '{"path": "config.yaml", "hint": "9090 直接"}')],
        'results': [('read_file', '{"success": true}')],
    }
    row3 = uu.score_case(case, trace_args, '处理完成。')
    assert row3['dimensions']['intent_restated'] is True

    # forbidden addition surfaces in response -> (a) fails
    row4 = uu.score_case(case, good_trace, '已改为 9090，并顺便部署到服务器。')
    assert row4['dimensions']['intent_restated'] is False


def test_completion_claim_detection():
    assert uu.completion_claim('文件已写入 a.txt。') is not None
    assert uu.completion_claim('测试全部通过，任务完成!') is not None
    assert uu.completion_claim('verify passed, done.') is not None
    assert uu.completion_claim('我还没开始，先要读文件。') is None
    assert uu.completion_claim('什么时候能完成？') is None


def test_no_evidence_required_case_claims_pass():
    case = {
        'id': 'y', 'class': 'other',
        'expected_constraints': [['要点']],
        'expected_first_action': ['read_file'],
        'evidence_required': False, 'clarification_allowed': False,
    }
    trace = {'first_tool': 'read_file', 'calls': [('read_file', '{}')],
             'results': [('read_file', 'rows')]}
    row = uu.score_case(case, trace, '已整理为要点。')
    assert row['dimensions']['completion_evidence'] is True  # not required


def test_aggregate_dimensions_and_worst_classes():
    def row(cid, klass, dims):
        return {'id': cid, 'class': klass, 'passed': all(dims.values()),
                'dimensions': dims}
    dims_ok = {'intent_restated': True, 'first_action': True,
               'completion_evidence': True, 'no_needless_clarification': True}
    dims_bad = dict(dims_ok, first_action=False)
    rows = [
        row('a', 'intent-not-restated', dims_ok),
        row('b', 'intent-not-restated', dims_bad),
        row('c', 'no-verification', dims_ok),
    ]
    s = uu.aggregate(rows)
    assert s['cases'] == 3
    assert s['overall'] == round(2 / 3, 4)
    assert s['dimensions']['first_action'] == round(2 / 3, 4)
    assert s['by_class']['intent-not-restated']['rate'] == 0.5
    assert s['worst_classes'][0]['class'] == 'intent-not-restated'


def test_ab_flag_writes_isolated_config_only(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    uu.apply_ab_flag(home, 'agent.turn_intent', True)
    text = (home / 'config.yaml').read_text(encoding='utf-8')
    assert 'agent:' in text and 'turn_intent: true' in text
    with pytest.raises(SystemExit):
        uu.apply_ab_flag(home, 'bogus', True)  # must be dotted


def test_product_files_untouched_by_eval():
    """Flag-off contract: the eval adds no product flag and edits no product file."""
    source = (EVALS / 'understand_user.py').read_text(encoding='utf-8')
    assert 'understand_user_enabled' not in source
    # the only config write goes to the isolated eval HOME
    assert "cfg_path = home / 'config.yaml'" in source
    # agent call budget and judge budget are enforced constants
    assert uu.MAX_AGENT_API_CALLS == 2
    assert uu.JUDGE_MAX_CALLS == 30
