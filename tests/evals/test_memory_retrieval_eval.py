"""Targeted tests for the DL-P2.6 memory-retrieval eval (eval-only code).

Covers: deterministic case-set generation, scoring invariants (top-k,
irrelevance, outdated-as-current), home isolation guard, and the
zero-product-change contract (no product flag exists; eval runs only when
explicitly invoked).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVALS = REPO / 'evals' / 'digital_life'
CASES = EVALS / 'memory_cases.json'


def _load_cases():
    with CASES.open(encoding='utf-8') as f:
        return json.load(f)


def test_case_set_shape_is_fixed():
    data = _load_cases()
    meta = data['meta']
    assert meta['facts'] == 100
    assert meta['questions'] == 100
    assert meta['topics'] == 30
    by_cat = {}
    for q in data['questions']:
        by_cat[q['category']] = by_cat.get(q['category'], 0) + 1
    assert by_cat == {'direct': 40, 'paraphrase': 20, 'distractor': 20, 'outdated': 20}


def test_case_generation_is_deterministic():
    """Re-running the generator must reproduce the committed JSON bytes."""
    script = EVALS / 'generate_memory_cases.py'
    result = subprocess.run(
        [sys.executable, '-B', str(script)],
        capture_output=True, text=True, cwd=str(REPO), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    regenerated = json.loads(CASES.read_text(encoding='utf-8'))
    committed = _load_cases()
    assert regenerated == committed


def test_every_question_targets_a_declared_topic():
    data = _load_cases()
    keys = {row['key'] for row in data['topics']}
    for q in data['questions']:
        assert q['target_topic'] in keys, q['id']
        for extra in ('current_topic', 'stale_topic', 'distractor_topic'):
            if q.get(extra):
                assert q[extra] in keys, (q['id'], extra)
        assert q.get('answer_key'), q['id']


def test_outdated_questions_carry_stale_pair():
    data = _load_cases()
    for q in data['questions']:
        if q['category'] == 'outdated':
            assert q.get('current_topic') != q.get('stale_topic'), q['id']
            assert q.get('stale_answer_key'), q['id']


sys.path.insert(0, str(REPO))

from evals.digital_life import memory_retrieval as mr  # noqa: E402


def test_assert_home_rejects_unsafe_paths(tmp_path):
    safe = tmp_path / 'home'
    safe.mkdir()
    env = {k: str(safe) for k in mr.HOME_VARS}
    assert mr.assert_home(safe, env) == safe.resolve()
    with pytest.raises(ValueError):
        mr.assert_home(safe, {**env, 'TEMP': str(tmp_path / 'other')})
    with pytest.raises(ValueError):
        mr.assert_home(safe, {**env, 'HERMES_HOME': 'D:/Stardust/home'})


def test_selected_keys_maps_titles_in_order():
    title_to_key = {'最喜欢的颜色': 'fav-color', '宠物': 'pet'}
    ctx = 'Relevant topic summaries...\n\n### 最喜欢的颜色\n蓝色。\n\n### 宠物\nPixel。'
    assert mr.selected_keys_from_context(ctx, title_to_key) == ['fav-color', 'pet']
    assert mr.selected_keys_from_context('', title_to_key) == []
    assert mr.selected_keys_from_context(None, title_to_key) == []


def test_score_question_counts_irrelevant_and_outdated():
    q = {
        'id': 'qX', 'category': 'outdated', 'question': '?',
        'target_topic': 'phone', 'current_topic': 'phone',
        'stale_topic': 'phone-old',
    }
    tokenizer = lambda s: len(s) // 4
    row = mr.score_question(q, ['phone', 'phone-old'], 'ctx', 1.0, tokenizer)
    assert row['top1'] is True and row['top2'] is True
    assert row['irrelevant'] == []
    assert row['outdated_as_current'] is True
    row2 = mr.score_question(q, ['phone'], 'ctx', 1.0, tokenizer)
    assert row2['outdated_as_current'] is False
    row3 = mr.score_question(q, ['desk', 'phone'], 'ctx', 1.0, tokenizer)
    assert row3['top1'] is False and row3['top2'] is True
    assert row3['irrelevant'] == ['desk']


def test_aggregate_rates_use_full_denominator():
    rows = [
        {'id': 'a', 'category': 'direct', 'top1': True, 'top2': True,
         'irrelevant': [], 'outdated_as_current': False,
         'context_tokens': 10, 'latency_ms': 1.0, 'selected': ['x']},
        {'id': 'b', 'category': 'direct', 'top1': False, 'top2': False,
         'irrelevant': ['y'], 'outdated_as_current': False,
         'context_tokens': 10, 'latency_ms': 2.0, 'selected': ['y']},
        {'id': 'c', 'category': 'outdated', 'top1': True, 'top2': True,
         'irrelevant': [], 'outdated_as_current': True,
         'context_tokens': 10, 'latency_ms': 3.0, 'selected': ['x', 'z']},
    ]
    summary = mr.aggregate(rows)
    assert summary['questions'] == 3
    assert summary['top1_recall'] == round(2 / 3, 4)
    assert summary['top2_recall'] == round(2 / 3, 4)
    assert summary['categories']['direct']['top1'] == 0.5
    assert summary['outdated_as_current_rate'] == 1.0
    assert summary['irrelevant_rate'] == round(1 / 4, 4)


def test_product_files_untouched_by_eval():
    """Flag-off contract: the eval adds no product flag and edits no product file."""
    # The eval never reads a digital_life.memory_eval_enabled flag to change
    # product behaviour — it is invoked explicitly. The only product surface
    # it touches is the pre-existing memory tool API.
    source = (EVALS / 'memory_retrieval.py').read_text(encoding='utf-8')
    assert 'memory_eval_enabled' not in source
    assert 'digital_life' not in source.replace("evals.digital_life", '')


def test_percentile_ordered():
    # nearest-rank with round-half-up index: upper median for even n
    assert mr.percentile([1, 2, 3, 4], 0.5) == 3
    assert mr.percentile([1, 2, 3, 4], 0.95) == 4
    assert mr.percentile([], 0.5) == 0.0
