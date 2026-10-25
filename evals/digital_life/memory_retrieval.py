"""Offline, opt-in long-term memory retrieval benchmark (DL-P2.6).

Measures how well the EXISTING long-term memory (topic summaries store +
per-turn recall path) retrieves the right facts, so later memory changes are
proven with before/after numbers on a fixed, committed case set.

Prior art (see RESULT-dl-p2-6.md for citations):
- LongMemEval (Wu et al. 2024, arXiv 2410.10813): question categories
  (paraphrase, distractors, temporal/outdated, knowledge update).
- LoCoMo (Maharana et al. 2024, arXiv 2402.17753): long-term conversational
  QA with adversarial/distractor categories.
- BEIR (Thakur et al. 2021, arXiv 2104.08663): recall@k / precision as the
  standard IR metrics for a fixed retrieval corpus.

The corpus is 100 synthetic facts in 25 fact-domains organised as 47 topic
summaries (32 static-domain topics + 15 evolving-domain entries, incl. 5
superseded duplicates), written through the REAL production write path
(``memory_tool`` -> ``upsert_topic_summary``) into an isolated HERMES_HOME.
100 questions (40 direct / 20 paraphrase / 20 distractor / 20 outdated) are
then asked through the REAL production recall path the agent uses at turn
time (``_memory_turn_start_and_prefetch`` on a real AIAgent with memory
enabled, fresh-session seen-set per question).

Measured: top-1/top-2 topic recall, per-category recall, irrelevant-recall
rate, outdated-returned-as-current rate, retrieval latency p50/p95, injected
context tokens per turn. Optional QA layer (``--qa``) answers through the
default configured model via magpie (at most 100 calls per variant); a
dump-all-memory control reports cost only.

Zero product behaviour change: eval-only code, no product file modified, no
product flag gates runtime behaviour; the runner is opt-in by being executed
explicitly with an isolated HOME.

Usage (from the repository root, repo venv, all six HOME vars isolated):

    python -B -m evals.digital_life.memory_retrieval \
        --home <isolated-home> --output <evidence>/results.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

HOME_VARS = ('HOME', 'USERPROFILE', 'STARDUST_HOME', 'HERMES_HOME', 'TEMP', 'TMP')
FORBIDDEN_HOME = Path('D:/Stardust/home').resolve()

TOPIC_RECALL_LIMIT = 2  # production default (agent/agent_init.py:1247)
QA_MAX_CALLS = 100  # spec: at most 100 QA calls per variant


def assert_home(home, env=None):
    """Validate isolation before importing product modules."""
    env = os.environ if env is None else env
    home = Path(home).resolve()
    for key in HOME_VARS:
        if not env.get(key):
            raise ValueError(f'{key} must be explicitly isolated')
        actual = Path(env[key]).resolve()
        if actual != home:
            raise ValueError(f'unsafe or mismatched {key}')
        if actual == FORBIDDEN_HOME or FORBIDDEN_HOME in actual.parents:
            raise ValueError(f'unsafe {key}: points into the running home')
    if home == REPO or REPO in home.parents:
        raise ValueError('HOME must be outside the source tree')
    return home


def load_cases():
    cases_path = Path(__file__).resolve().parent / 'memory_cases.json'
    with cases_path.open(encoding='utf-8') as f:
        return json.load(f)


def default_tokenizer():
    """tiktoken when available, else bytes/4. The report labels which."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding('o200k_base')
        return (lambda s: len(enc.encode(s, disallowed_special=())), 'o200k_base')
    except Exception:
        return (lambda s: max(1, len(s.encode('utf-8')) // 4)), 'bytes/4'


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[idx]


def write_corpus(home: Path, provenance: bool = False):
    """Write the fixed 100-fact corpus through the REAL memory write path.

    provenance=True (the DL-P2.7 flag-on variant) stamps every write with
    explicit bi-temporal facts the generator knows semantically: the current
    track is observed 2026-10-20, each stale fork records an older observation
    (2026-09-01) that expired 2026-10-01 — mirroring how a writer archives a
    superseded fact behind a newer one (Zep/Graphiti valid_at/invalid_at).
    """
    from tools.memory_tool import memory_tool

    cases = load_cases()
    writes = cases['writes']
    expected = cases['meta']['writes']
    if len(writes) != expected:
        raise ValueError(f'case set drifted: {len(writes)} writes, expected {expected}')
    count = 0
    for payload in writes:
        kwargs = {}
        if provenance:
            if payload['phase'] == 'stale-fork':
                kwargs = {'observed_at': '2026-09-01', 'valid_until': '2026-10-01'}
            else:
                kwargs = {'observed_at': '2026-10-20'}
        result = memory_tool(
            action='add', target='topic',
            topic=payload['topic'], title=payload['title'],
            keywords=payload['keywords'], content=payload['summary'],
            **kwargs,
        )
        parsed = json.loads(result)
        if not parsed.get('success'):
            raise RuntimeError(f'corpus write failed for {payload["topic"]}: {parsed}')
        count += 1
    return count


def seed_fact_store(home: Path):
    """DL-P2.9 flag-on variant: seed FACTS.json from the corpus semantics.

    For every outdated-question domain the generator knows both values: the
    stale fork (e.g. "阿豪的手机是 Pixel 8") and the current answer key. The
    seed replays exactly what the pipeline would have stored: old fact added,
    then closed (valid_to set) when the new value arrived — never deleted.
    """
    from tools import fact_store as fs

    cases = load_cases()
    stale_by_topic = {w['topic']: w for w in cases['writes'] if w['phase'] == 'stale-fork'}
    seen_domains = set()
    added = closed = 0
    for q in cases['questions']:
        if q['category'] != 'outdated' or q.get('stale_topic') not in stale_by_topic:
            continue
        domain = q['current_topic']
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        stale = stale_by_topic[q['stale_topic']]
        old = fs.add_fact(
            subject=domain, predicate='现状',
            content=stale['summary'].split('（')[0].rstrip('。') + '。',
            source='fact-seed', source_session_id='corpus-seed',
            observed_at='2026-09-01T00:00:00+00:00',
        )
        added += 1
        if old.get('success'):
            closed_row = fs.close_fact(
                old['target_id'], reason='superseded',
                valid_to='2026-10-01T00:00:00+00:00',
            )
            closed += int(bool(closed_row.get('success')))
        fs.add_fact(
            subject=domain, predicate='现状',
            content=q['answer_key'].rstrip('。') + '。',
            source='fact-seed', source_session_id='corpus-seed',
            observed_at='2026-10-20T00:00:00+00:00',
        )
        added += 1
    return {'domains': len(seen_domains), 'added': added, 'closed': closed}


def make_agent(session_id: str):
    """Build a REAL AIAgent with memory enabled (skip_memory=False).

    The FakeModel endpoint never needs to answer a completion here: the
    recall path runs synchronously before any API request is made.
    """
    from evals.assistant_scenarios.fake_model import FakeModel, Reply
    from hermes_state import SessionDB
    from run_agent import AIAgent

    endpoint = FakeModel(lambda request: Reply(text='ok'))
    endpoint.__enter__()  # serve thread; closed by the caller via shutdown()
    db = SessionDB()
    agent = AIAgent(
        base_url=endpoint.base_url, api_key='offline-eval', provider='custom',
        api_mode='chat_completions', model='memory-eval-local', session_id=session_id,
        session_db=db, enabled_toolsets=['memory'], max_iterations=12,
        quiet_mode=True, skip_context_files=True, skip_memory=False,
        skip_background_review=True,
    )
    return agent, endpoint, db


def close_endpoint(endpoint):
    try:
        endpoint._server.shutdown()
        endpoint._server.server_close()
    except Exception:
        pass


def close_quietly(obj, attr='close'):
    try:
        getattr(obj, attr)()
    except Exception:
        pass


def selected_keys_from_context(context, title_to_key):
    """Map injected '### <title>' headers back to topic keys (ordered)."""
    if not context:
        return []
    keys = []
    for line in context.splitlines():
        if line.startswith('### '):
            key = title_to_key.get(line[4:].strip())
            if key:
                keys.append(key)
    return keys


def run_retrieval(questions, title_to_key, tokenizer):
    """Ask every question through the production recall path.

    Each question runs with a fresh session/seen-set, matching production
    new-session semantics; ``_topic_recall_limit``/``_topic_recall_char_budget``
    stay at the production defaults the agent sets in agent_init.
    """
    from agent.turn_context import _memory_turn_start_and_prefetch

    rows = []
    for index, q in enumerate(questions):
        agent, endpoint, db = make_agent(f'memory-eval-q{index:03d}')
        try:
            started = time.perf_counter()
            context = _memory_turn_start_and_prefetch(agent, q['question'])
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        finally:
            close_quietly(agent)
            close_endpoint(endpoint)
            close_quietly(db)
        selected = selected_keys_from_context(context, title_to_key)
        rows.append(score_question(q, selected, context, elapsed_ms, tokenizer))
    return rows


def score_question(q, selected, context, elapsed_ms, tokenizer):
    """Score one question against the selected topic keys (BEIR-style)."""
    target = q['target_topic']
    related = {target}
    for extra in ('current_topic', 'stale_topic', 'distractor_topic'):
        if q.get(extra):
            related.add(q[extra])
    irrelevant = [k for k in selected if k not in related]
    stale_selected = bool(q.get('stale_topic')) and q.get('stale_topic') in selected[:2]
    outdated_as_current = stale_selected
    # A retained stale topic is acceptable only when its injected copy is
    # explicitly marked outdated. Never infer this from a current fact appended
    # elsewhere: the original topic can still assert the superseded value.
    if stale_selected:
        from tools.topic_memory_store import get_topic_summary

        stale = get_topic_summary(q['stale_topic'])
        if stale:
            heading = '### ' + str(stale.get('title') or stale.get('key') or '')
            body = str(context or '').split(heading, 1)
            if len(body) == 2:
                injected = body[1].split('\n### ', 1)[0].strip()
                outdated_as_current = not injected.startswith('[outdated, now:')
    return {
        'id': q['id'],
        'category': q['category'],
        'question': q['question'],
        'answer_fact': q.get('answer_fact', ''),
        'target_topic': target,
        'selected': selected,
        'top1': target in selected[:1],
        'top2': target in selected[:2],
        'irrelevant': irrelevant,
        'outdated_as_current': outdated_as_current,
        'context_chars': len(context or ''),
        'context_tokens': tokenizer(context or ''),
        'latency_ms': round(elapsed_ms, 3),
    }


def aggregate(rows):
    """Aggregate per-question rows into the report metrics."""
    def rate(sel, field):
        return round(sum(1 for r in sel if r[field]) / len(sel), 4) if sel else 0.0

    categories = {}
    for name in ('direct', 'paraphrase', 'distractor', 'outdated'):
        sel = [r for r in rows if r['category'] == name]
        categories[name] = {
            'questions': len(sel),
            'top1': rate(sel, 'top1'),
            'top2': rate(sel, 'top2'),
        }
    outdated_rows = [r for r in rows if r['category'] == 'outdated']
    latencies = [r['latency_ms'] for r in rows]
    selected_total = sum(len(r['selected']) for r in rows)
    irrelevant_count = sum(len(r['irrelevant']) for r in rows)
    tokens = [r['context_tokens'] for r in rows]
    return {
        'questions': len(rows),
        'top1_recall': rate(rows, 'top1'),
        'top2_recall': rate(rows, 'top2'),
        'categories': categories,
        'irrelevant_rate': round(irrelevant_count / selected_total, 4) if selected_total else 0.0,
        'irrelevant_selected_count': irrelevant_count,
        'selected_total': selected_total,
        'outdated_as_current_rate': rate(outdated_rows, 'outdated_as_current'),
        'latency_p50_ms': round(percentile(latencies, 0.50), 3),
        'latency_p95_ms': round(percentile(latencies, 0.95), 3),
        'context_tokens_mean': round(statistics.fmean(tokens), 1) if tokens else 0.0,
        'context_tokens_max': max(tokens) if tokens else 0,
    }


def dump_all_control(topics_meta, tokenizer):
    """'Dump all memory into the prompt' cost control (report-only)."""
    from tools.topic_memory_store import format_topic_recall, list_topic_summaries

    summaries = list_topic_summaries(include_summary=True)
    blob = format_topic_recall(summaries)
    return {
        'topics': len(summaries),
        'chars': len(blob),
        'tokens': tokenizer(blob),
        'note': 'cost control only, not a proposed solution',
    }


def qa_answer(question, context_block):
    """One QA call through the default configured model via magpie."""
    import urllib.request

    endpoint = os.environ.get('DL_MEMORY_QA_BASE_URL', 'http://127.0.0.1:3425/v1')
    payload = {
        'model': os.environ.get('DL_MEMORY_QA_MODEL', 'gpt-6-luna'),
        'messages': [
            {'role': 'system', 'content': 'Answer using ONLY the memory context. '
                                          'Reply with the bare answer, no explanation.'},
            {'role': 'user', 'content': f'Memory context:\n{context_block}\n\nQuestion: {question}'},
        ],
        'max_tokens': 120,
    }
    req = urllib.request.Request(
        endpoint.rstrip('/') + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def _value_runs(text: str, exclude: str) -> list[str]:
    """Latin/digit runs (len>=2) in text that do not appear in exclude."""
    norm = lambda s: ''.join(ch.lower() for ch in str(s or '') if ch.isalnum())
    answer_norm, exclude_norm = norm(text), norm(exclude)
    runs = re.findall(r'[A-Za-z0-9][A-Za-z0-9 .+-]{1,}', str(text or ''))
    return [r for r in runs if norm(r) and norm(r) not in exclude_norm]


def run_qa(rows, questions_by_id, tokenizer, max_calls=QA_MAX_CALLS):
    """Optional QA layer over the retrieval rows (same model, same budget).

    Correct = the answer states the current fact and never the superseded one.
    A containment check alone is too brittle for Chinese paraphrase ('现在用'
    vs '目前用的是'), so grading is two-tier for outdated questions:
    (a) the normalized key is contained in the answer or vice versa, or
    (b) the key's distinguishing latin/digit value runs (those absent from the
    stale key) all appear in the answer. Either way, a stale value in the
    answer fails the question (stale answer = wrong; stricter than
    LongMemEval, which counts 'both mentioned' as correct).
    """
    answered, correct, skipped = 0, 0, 0
    per_category = {}
    norm = lambda s: ''.join(ch.lower() for ch in str(s or '') if ch.isalnum())
    for row in rows[:max_calls]:
        q = questions_by_id[row['id']]
        expected = q.get('answer_key') or ''
        if not expected:
            skipped += 1
            continue
        context_block = row.get('context_block') or ''
        per_cat = per_category.setdefault(row['category'], {'asked': 0, 'correct': 0})
        if not context_block:
            # Retrieval returned nothing: the model cannot know the answer.
            per_cat['asked'] += 1
            answered += 1
            continue
        try:
            answer = qa_answer(row['question'], context_block)
        except Exception as exc:
            return {'error': f'QA layer aborted: {exc}', 'answered_before_abort': answered}
        answered += 1
        stale_key = q.get('stale_answer_key') or ''
        ok = norm(expected) in norm(answer) or norm(answer) in norm(expected)
        if not ok and stale_key:
            current_runs = _value_runs(expected, stale_key)
            if current_runs:
                ok = all(norm(run) in norm(answer) for run in current_runs)
        if ok and stale_key:
            ok = norm(stale_key) not in norm(answer)
        per_cat['asked'] += 1
        per_cat['correct'] += int(ok)
        correct += int(ok)
    return {
        'answered': answered,
        'skipped_no_expected': skipped,
        'correct': correct,
        'accuracy': round(correct / answered, 4) if answered else 0.0,
        'per_category': {
            name: {'asked': v['asked'], 'accuracy': round(v['correct'] / v['asked'], 4) if v['asked'] else 0.0}
            for name, v in per_category.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qa', action='store_true', help='run the optional QA layer via magpie')
    parser.add_argument('--provenance', action='store_true',
                        help='flag-on variant: stamp the corpus with bi-temporal provenance '
                             'and set memory.topic_provenance_enabled=true in the home config')
    parser.add_argument('--hybrid', action='store_true',
                        help='flag-on variant: set memory.hybrid_recall=true in the home config '
                             '(summary-body scoring + same-domain decay + BM25/RRF fusion)')
    parser.add_argument('--semantic', action='store_true',
                        help='flag-on variant: additionally set memory.semantic_recall=true '
                             '(local embedding fill-in leg on starved lexical gates; requires '
                             'fastembed, downloads bge-small-zh-v1.5 on first starved recall)')
    parser.add_argument('--fact-pipeline', action='store_true',
                        help='flag-on variant: set memory.fact_pipeline=true and seed FACTS.json '
                             'from the corpus (stale forks closed, current values open)')
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.provenance or args.hybrid or args.semantic or args.fact_pipeline:
        # The agent reads memory.* flags through the normal config path; write
        # them into the isolated home before corpus write.
        cfg = home / 'config.yaml'
        if cfg.exists():
            raise SystemExit(f'refusing to overwrite existing config: {cfg}')
        flags = []
        if args.provenance:
            flags.append('  topic_provenance_enabled: true')
        if args.hybrid or args.semantic:
            flags.append('  hybrid_recall: true')
        if args.semantic:
            flags.append('  semantic_recall: true')
        if args.fact_pipeline:
            flags.append('  fact_pipeline: true')
        cfg.write_text('memory:\n' + '\n'.join(flags) + '\n', encoding='utf-8')

    tokenizer, tokenizer_label = default_tokenizer()

    # 1) Corpus through the real write path (fresh home each invocation).
    topics_file = home / 'memories' / 'TOPICS.json'
    if topics_file.exists():
        raise SystemExit(f'refusing to overwrite populated home: {topics_file}')
    count = write_corpus(home, provenance=args.provenance)
    if args.fact_pipeline:
        seed_fact_store(home)

    cases = load_cases()
    topics_meta = {row['key']: row for row in cases['topics']}
    title_to_key = {row['title']: key for key, row in topics_meta.items()}
    questions = cases['questions']
    if len(questions) != 100:
        raise SystemExit(f'case set drifted: {len(questions)} questions, expected 100')

    # 2) Retrieval through the real recall path.
    rows = run_retrieval(questions, title_to_key, tokenizer)
    summary = aggregate(rows)

    # 3) Dump-all cost control.
    control = dump_all_control(topics_meta, tokenizer)

    result = {
        'meta': {
            'home': str(home),
            'corpus_writes': count,
            'topics': len(topics_meta),
            'questions': len(questions),
            'tokenizer': tokenizer_label,
            'topic_recall_limit': TOPIC_RECALL_LIMIT,
            'provenance': bool(args.provenance),
            'hybrid': bool(args.hybrid) or bool(args.semantic),
            'semantic': bool(args.semantic),
            'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        },
        'retrieval': summary,
        'dump_all_control': control,
        'per_question': rows,
    }

    # 4) Optional QA layer (bounded, same model + context budget per variant).
    if args.qa:
        questions_by_id = {q['id']: q for q in questions}
        # Rebuild the exact injected context per question for the QA prompt.
        from agent.turn_context import _memory_turn_start_and_prefetch
        for index, q in enumerate(questions):
            agent, endpoint, db = make_agent(f'memory-eval-qa{index:03d}')
            try:
                rows[index]['context_block'] = _memory_turn_start_and_prefetch(agent, q['question'])
            finally:
                close_quietly(agent)
                close_endpoint(endpoint)
                close_quietly(db)
        result['qa'] = run_qa(rows, questions_by_id, tokenizer)

    with output.open('w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps({'retrieval': summary, 'control': control}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
