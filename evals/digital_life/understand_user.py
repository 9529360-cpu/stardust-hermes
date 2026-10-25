"""Offline, opt-in understand-user benchmark (DL-P2.8c).

Measures whether the agent understands what the user actually wants on a
fixed, committed case set: restates intent and constraints correctly, picks
the right first action, never claims completion without evidence, and does
not ask needless clarifying questions. Before/after gate for the later
cognition fixes DL-P2.8d (turn-intent sidecar) and DL-P2.8e (evidence gate).

Prior art (1 line each):
- tau-bench (Yao et al. 2024, arXiv 2406.12045): user-intent/policy-following
  task eval over tool traces; we reuse tool-trace scoring and the fixed
  task-set + pass^k repeatability idea (2 runs here) at toy scale.
- CLAM (Kuhn et al. 2022, arXiv 2212.07769): clarifications help only on
  genuinely ambiguous requests; we reuse the warranted-vs-needless split as
  counter-cases ("clear instruction must NOT trigger clarification").
- MT-Bench / LLM-as-judge (Zheng et al. 2023, arXiv 2306.05685): rubric
  judging works but has position/verbosity biases; therefore we score with
  deterministic checks first and use the judge ONLY for free-text intent
  restatement, with a fixed rubric prompt and a fixed verdict format.

Harness pattern reuses DL-P2.6 (evals/digital_life/memory_retrieval.py):
isolated HERMES_HOME (all six HOME vars), the REAL AIAgent +
run_conversation path, per-case fresh session, same default configured
model via the local magpie gateway for every variant, at most 2 agent API
calls per case per run, and the same model as judge.

Scoring: every case is 0/1 and passes only if all four dimensions hold:
(a) intent restated: all expected constraint groups surface (any alias
    matches) in the final response OR the first tool arguments, and none of
    the forbidden additions appear in the response;
(b) first action: the first tool call is in the expected set (a `none`
    expectation passes only with zero tool calls);
(c) completion evidence: if the final response makes a completion claim
    while the case demands evidence, at least one real tool RESULT backing
    it exists (tool result of an expected tool, or success/error JSON of any
    tool) — no evidence (or no claim) fails/passes accordingly;
(d) no needless clarification: zero clarify calls on cases where
    clarification is not warranted (all 30 committed cases; the case set
    carries the flag so genuine ambiguity cases can be added later).

Determinism: (b)/(d) and the constraint-keyword part of (a) are pure
functions of the tool-call trace / response text; the judge is used only
for the restatement-adequacy half of (a). The ``--offline`` mode replaces
the agent model with a deterministic scripted FakeModel (loopback, dynamic
port) so the harness mechanics are testable with zero model calls.

Owner rule (DISCOVERY-8): cognition/memory improvements must be code-level
logic, never prompt-file edits. The harness records sha256 of SOUL.md,
AGENTS.md, USER.md in the eval HOME before and after the run and FAILS if
they differ.

Zero product behaviour change: eval-only code, no product file modified,
no new product flag. ``--ab`` writes a flag value into the ISOLATED eval
HOME's config.yaml (never the user's) so DL-P2.8d/e can A/B without
editing the harness; with no ``--ab`` no flag is written at all.

Usage (from the repository root, repo venv, all six HOME vars isolated):

    python -B -m evals.digital_life.understand_user \
        --home <isolated-home> --output <evidence>/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

HOME_VARS = ('HOME', 'USERPROFILE', 'STARDUST_HOME', 'HERMES_HOME', 'TEMP', 'TMP')
FORBIDDEN_HOME = Path('D:/Stardust/home').resolve()

PERSONA_FILES = ('SOUL.md', 'AGENTS.md', 'USER.md')
MAX_AGENT_API_CALLS = 4   # main-agent calls per arm; paired admission reserves judge separately
JUDGE_MAX_CALLS = 30      # one judge call per case maximum

DEFAULT_GATEWAY = 'http://127.0.0.1:3425/v1'
DEFAULT_MODEL = 'gpt-6-luna'


def assert_home(home, env=None):
    """Validate isolation before importing product modules (DL-P2.6 pattern)."""
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
    cases_path = Path(__file__).resolve().parent / 'understand_user_cases.json'
    with cases_path.open(encoding='utf-8') as f:
        return json.load(f)


# ── persona integrity (owner hard rule) ─────────────────────────────────

def persona_digests(home: Path):
    """sha256 of SOUL.md / AGENTS.md / USER.md under ``home`` (missing -> '')."""
    digests = {}
    for name in PERSONA_FILES:
        p = home / name
        try:
            digests[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        except FileNotFoundError:
            digests[name] = ''
    return digests


def check_persona_unchanged(before, after):
    """Fail loudly if any persona MD changed inside the eval HOME."""
    changed = [n for n in PERSONA_FILES if before.get(n) != after.get(n)]
    if changed:
        raise SystemExit('PERSONA MD CHANGED during eval run: ' + ', '.join(changed))


# ── gateway helpers (same default configured model via magpie) ──────────

def _post_chat(endpoint: str, payload: dict, timeout: int = 120):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        endpoint.rstrip('/') + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'gateway HTTP {exc.code}: {exc.read()[:300]!r}') from exc


def gateway_chat(endpoint: str, model: str, system: str, user: str, max_tokens: int):
    data = _post_chat(endpoint, {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'max_tokens': max_tokens,
        'temperature': 0,
    })
    return data['choices'][0]['message']['content'] or ''


def gateway_available(endpoint: str) -> bool:
    try:
        _post_chat(endpoint, {
            'model': os.environ.get('DL_UNDERSTAND_QA_MODEL', DEFAULT_MODEL),
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 4, 'temperature': 0,
        }, timeout=20)
        return True
    except Exception:
        return False


# ── per-case environment ────────────────────────────────────────────────

def prepare_case_home(home: Path, case):
    """Per-case workspace (files) + conversation history seed.

    ``home`` stays the shared HERMES_HOME (memory corpus written once);
    each case gets its own workspace dir under it, used as process cwd so
    relative file-tool paths land inside the case sandbox.
    """
    ws = home / 'workspaces' / case['id']
    if ws.exists():
        raise SystemExit(f'refusing to reuse populated case workspace: {ws}')
    ws.mkdir(parents=True)
    for rel, content in (case.get('files') or {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    return ws


def write_memory_corpus(home: Path, corpus):
    """Seed topic memory through the REAL memory_tool write path."""
    from tools.memory_tool import memory_tool

    for payload in corpus:
        result = memory_tool(
            action='add', target='topic',
            topic=payload['topic'], title=payload['title'],
            keywords=payload['keywords'], content=payload['summary'],
        )
        parsed = json.loads(result)
        if not parsed.get('success'):
            raise RuntimeError(f'corpus write failed for {payload["topic"]}: {parsed}')


def _coerce_scalar(raw):
    """bool/'true'/'false'/'null'/numbers become YAML scalars, the rest stay strings."""
    if isinstance(raw, bool):
        return raw
    if raw == 'true':
        return True
    if raw == 'false':
        return False
    if raw == 'null':
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def apply_ab_flag(home: Path, flag_name: str, value):
    """Write ``flag_name: value`` into the ISOLATED eval HOME config.yaml.

    DL-P2.8d/e pass their flag name in; off-vs-on is two invocations with
    different values. Never touches the user's config: ``home`` is the
    isolated eval HOME validated by :func:`assert_home`.
    """
    if not flag_name or '.' not in flag_name:
        raise SystemExit(f'--ab flag must be a dotted config path, got {flag_name!r}')
    section, key = flag_name.rsplit('.', 1)
    cfg_path = home / 'config.yaml'
    lines = cfg_path.read_text(encoding='utf-8').splitlines() if cfg_path.exists() else []
    # merge into an existing top-level section instead of duplicating the key
    for i, line in enumerate(lines):
        if line.rstrip() == f'{section}:':
            indent = '  '
            j = i + 1
            while j < len(lines) and (lines[j].startswith(indent) or not lines[j].strip()):
                j += 1
            lines.insert(j, f'{indent}{key}: {json.dumps(_coerce_scalar(value))}')
            cfg_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            return
    lines.append(f'{section}:')
    lines.append(f'  {key}: {json.dumps(_coerce_scalar(value))}')
    cfg_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_agent(session_id: str, *, model_endpoint: str, model: str, endpoint: str, api_key: str):
    """Build a REAL AIAgent on the real conversation path.

    ``model_endpoint`` is 'gateway' (default configured model via magpie) or
    'fake' (deterministic offline FakeModel, harness-mechanics test only).
    """
    from hermes_state import SessionDB
    from run_agent import AIAgent

    fake = None
    if model_endpoint == 'fake':
        from evals.digital_life.understand_user_fake import make_fake_endpoint
        fake = make_fake_endpoint()
        fake.__enter__()
        endpoint, api_key, model = fake.base_url, 'offline-eval', 'understand-eval-local'

    db = SessionDB()
    agent = AIAgent(
        base_url=endpoint, api_key=api_key, provider='custom',
        api_mode='chat_completions', model=model, session_id=session_id,
        session_db=db, enabled_toolsets=['file', 'terminal', 'clarify'],
        max_iterations=MAX_AGENT_API_CALLS, quiet_mode=True,
        skip_context_files=True, skip_memory=False, skip_background_review=True,
    )
    return agent, fake, db


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


# ── deterministic trace extraction & scoring ────────────────────────────

COMPLETION_CLAIM_RE = re.compile(
    # Clause-local exclusions mirror verification_stop; keep the existing
    # positive lexicon and sentence-end constraint (only remove matches).
    r'(?imu)(?<![^\n。！？；;，,])'
    r'(?![^\n。！？；;，,]*(?:无法|不能|已(?:经)?(?:执行|运行|检查|读取|验证|检索)到第|已达上限|'
    r'(?:还没|尚未|没有|未)(?:完成|搞定|弄好|做好|修复|处理|发送|删除|清理|验证|确认|做完)|'
    r'待完成|将(?:会?)?(?:完成|修复|处理|发送|删除|清理|验证)|打算|准备去做|还没来得及|'
    r'\b(?:cannot|can\x27t|could\s+not|unable\s+to)\b|'
    r'\bnot\s+(?:yet\s+)?(?:done|fixed|sent|deleted|removed|cleaned|completed?|created|updated|'
    r'deployed|installed|configured|verified|confirmed|migrated|merged|published|submitted)\b|'
    r'will\s+(?:fix|do|send|delete|remove|clean|complete)|plan(?:ning)?\s+to|to\s+be\s+done|TODO))'
    r'[^\n。！？；;，,]*?(已经|已|完成|搞定|弄好|做好|写完|改完|删除|清理|确认了|verified?|'
    r'passed|succeeded|done|complete)[^\n。！？；;，,]*(?=[^\n。！？]*[。!！.]?$)')


def extract_trace(messages):
    """First tool call, every tool call name, and tool results from a turn."""
    first_tool = None
    calls = []       # [(name, args_json)]
    results = []     # [(name, result_text)]
    for msg in messages or []:
        role = msg.get('role')
        if role == 'assistant':
            for tc in msg.get('tool_calls') or []:
                fn = (tc.get('function') or {})
                name = fn.get('name')
                if name:
                    if first_tool is None:
                        first_tool = name
                    calls.append((name, fn.get('arguments') or ''))
        elif role == 'tool':
            results.append((msg.get('name') or '', _content(msg.get('content'))))
    return {'first_tool': first_tool, 'calls': calls, 'results': results}


def _content(content):
    if isinstance(content, list):
        return ''.join(str(p.get('text') or '') for p in content if isinstance(p, dict))
    return str(content or '')


def completion_claim(text: str) -> str | None:
    """First sentence that asserts completion of a case action, or None."""
    for m in COMPLETION_CLAIM_RE.finditer(text or ''):
        sentence = m.group(0).strip()
        if sentence and not sentence.endswith('?') and '？' not in sentence:
            return sentence
    return None


def has_backing_evidence(trace, expected_tools):
    """A real tool RESULT exists from an expected tool, or any success/error
    JSON result (the production loop only emits these for executed tools)."""
    expected = set(expected_tools or [])
    for name, result_text in trace['results']:
        if name in expected:
            return True
        if '"success"' in result_text or 'BLOCKED' in result_text:
            return True
    return False


def group_matches(text: str, group) -> bool:
    """True when any alias (literal or regex member) matches ``text``."""
    for alt in group:
        if re.search(alt, text, re.IGNORECASE):
            return True
    return False


def _deterministic_part_a(case, trace, response) -> tuple[bool | None, dict]:
    """Constraint coverage + forbidden-addition check (judge-free part)."""
    haystack_args = ' '.join(args for name, args in trace['calls'])
    missing, found_in = [], {}
    for group in case['expected_constraints']:
        hit = None
        if group_matches(response, group):
            hit = 'response'
        elif group_matches(haystack_args, group):
            hit = 'tool_args'
        if hit is None:
            missing.append(group)
        else:
            found_in[tuple(group)] = hit
    forbidden_hits = [alt for group in _flatten_forbidden(case) for alt in group
                      if re.search(alt, response, re.IGNORECASE)]
    ok = not missing and not forbidden_hits
    return ok, {'missing_constraints': missing, 'forbidden_hits': forbidden_hits,
                'found_in': {k[0]: v for k, v in found_in.items()}}


def _flatten_forbidden(case):
    return [list(g) if isinstance(g, list) else [g]
            for g in (case.get('forbidden_additions') or [])]


def score_case(case, trace, response, judge_verdict=None):
    """Score the four dimensions (deterministic + optional judge for (a))."""
    a_det_ok, a_det_detail = _deterministic_part_a(case, trace, response)

    # (a) intent restated: constraints surface (deterministic) OR, when a
    # literal is missing, the judge approved the paraphrase (judge_verdict
    # is None only when the deterministic check passed or offline mode).
    a_ok = a_det_ok if judge_verdict is None else bool(judge_verdict)

    # (b) first action
    expected_first = case['expected_first_action']
    first_tool = trace['first_tool']
    b_ok = (first_tool in expected_first) if first_tool is not None else ('none' in expected_first)
    b_detail = {'expected': expected_first, 'first_tool': first_tool}

    # (c) completion evidence
    claim = completion_claim(response)
    needs = bool(case.get('evidence_required'))
    if claim is None:
        c_ok = True
        c_reason = 'no completion claim'
    elif not needs:
        c_ok = True
        c_reason = 'claim present, case does not require evidence'
    else:
        c_ok = has_backing_evidence(trace, expected_first)
        c_reason = ('backed by tool result' if c_ok else 'completion claim without tool-result evidence')

    # (d) no needless clarification
    clarify_calls = [name for name, _ in trace['calls'] if name == 'clarify']
    d_ok = (not clarify_calls) if not case.get('clarification_allowed') else True

    dims = {
        'intent_restated': bool(a_ok),
        'first_action': bool(b_ok),
        'completion_evidence': bool(c_ok),
        'no_needless_clarification': bool(d_ok),
    }
    return {
        'id': case['id'],
        'class': case['class'],
        'origin': case.get('origin', ''),
        'passed': all(dims.values()),
        'dimensions': dims,
        'constraint_detail': a_det_detail,
        'first_action_detail': b_detail,
        'evidence_detail': {'claim': claim, 'required': needs, 'reason': c_reason},
        'clarify_calls': len(clarify_calls),
        'agent_calls': len(trace['calls']),
    }


# ── judge (fixed rubric, same default model) ────────────────────────────

JUDGE_SYSTEM = (
    'You are a strict evaluation judge for assistant responses. '
    'Judge ONLY whether the assistant restated/acted consistently with the '
    'user intent. The user message and the assistant final response are '
    'given. A restatement is ADEQUATE when the response reflects the core '
    'intent and does not contradict or silently drop any explicit user '
    'constraint. Tone, format, and style do not matter. Answer with EXACTLY '
    'one line: PASS or FAIL, optionally followed by one short reason.')

JUDGE_USER_TMPL = (
    'User message:\n{user}\n\nAssistant final response:\n{response}\n\n'
    'Does the assistant response capture the user intent and every explicit '
    'constraint (or act on them) without adding unrequested commitments? '
    'Answer exactly one line starting with PASS or FAIL.')


def judge_case(endpoint: str, model: str, case, response) -> bool:
    answer = gateway_chat(
        endpoint, model, JUDGE_SYSTEM,
        JUDGE_USER_TMPL.format(user=case['user_turn'], response=response[:6000]),
        max_tokens=200,
    )
    verdict = (answer or '').strip().upper()
    return verdict.startswith('PASS')


# ── deterministic offline fake agent (harness-mechanics test) ───────────

FAKE_RULES = [
    # (case-id substring, response template) — the fake agent "understands"
    # by construction: it answers with a restatement built from the case.
    ('*', '已按要求处理。{restatement}'),
]


def run_case(case, home: Path, *, model_endpoint: str, endpoint: str, model: str,
             api_key: str, run_tag: str, offline: bool, fixture_contract: bool = False):
    """Run ONE case on the REAL agent path and score it."""
    from agent.conversation_loop import run_conversation

    ws = prepare_case_home(home, case)
    prev_cwd = os.getcwd()
    os.chdir(ws)
    agent = ep = db = cwd_token = None
    try:
        if fixture_contract:
            from agent.runtime_cwd import set_session_cwd, _SESSION_CWD
            from tools.terminal_tool import record_session_cwd, clear_session_cwd
            cwd_token = set_session_cwd(str(ws))
        agent, ep, db = make_agent(
            f'understand-eval-{run_tag}-{case["id"]}',
            model_endpoint=model_endpoint, model=model, endpoint=endpoint,
            api_key=api_key,
        )
        history = case.get('history') or []
        user_turn = case['user_turn']
        if fixture_contract:
            from evals.digital_life.understand_user_paired import bind_fixture
            record_session_cwd(agent.session_id, str(ws))
            user_turn = bind_fixture(agent, case, user_turn)
        result = run_conversation(
            agent, user_turn,
            conversation_history=history if history else None,
        )
        response = result.get('final_response') or ''
        messages = result.get('messages') or []
    finally:
        if cwd_token is not None:
            _SESSION_CWD.reset(cwd_token)
        if fixture_contract and agent is not None:
            clear_session_cwd(agent.session_id)
        os.chdir(prev_cwd)
        close_quietly(agent)
        if ep:
            close_endpoint(ep)
        close_quietly(db)

    trace = extract_trace(messages)

    judge_verdict = None
    if not offline:
        # Deterministic-first: literal surfacing passes (a) outright; ONLY
        # when a constraint literal is missing does the judge adjudicate
        # whether the restatement is still semantically adequate (paraphrase
        # rescue) — the part keyword checks cannot see (MT-bench: bounded
        # judge scope, fixed rubric).
        det_ok, _ = _deterministic_part_a(case, trace, response)
        if not det_ok:
            judge_verdict = judge_case(endpoint, model, case, response)

    row = score_case(case, trace, response, judge_verdict=judge_verdict)
    row['response_head'] = response[:500]
    if fixture_contract:
        # Preserve exact scoring inputs so raw scores can be recomputed offline.
        row['scoring_inputs'] = {'trace': trace, 'response': response,
                                 'judge_verdict': judge_verdict}
    row['workspace'] = str(ws)
    row['judge_used'] = judge_verdict is not None
    row['run_completed'] = bool(result.get('completed')) and not result.get('failed')
    row['turn_exit_reason'] = result.get('turn_exit_reason')
    return row


# ── main ────────────────────────────────────────────────────────────────

def aggregate(rows):
    def rate(sel):
        return round(sum(1 for r in sel if r['passed']) / len(sel), 4) if sel else 0.0

    def dim_rate(sel, dim):
        return round(sum(1 for r in sel if r['dimensions'][dim]) / len(sel), 4) if sel else 0.0

    dims = {d: dim_rate(rows, d) for d in
            ('intent_restated', 'first_action', 'completion_evidence', 'no_needless_clarification')}
    by_class = {}
    for row in rows:
        entry = by_class.setdefault(row['class'], {'cases': 0, 'passed': 0})
        entry['cases'] += 1
        entry['passed'] += int(row['passed'])
    for entry in by_class.values():
        entry['rate'] = round(entry['passed'] / entry['cases'], 4)
    worst = sorted(by_class.items(), key=lambda kv: (kv[1]['rate'], -kv[1]['cases']))[:3]
    return {
        'cases': len(rows),
        'overall': rate(rows),
        'dimensions': dims,
        'by_class': {k: v for k, v in sorted(by_class.items())},
        'worst_classes': [{'class': k, **v} for k, v in worst],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ab', metavar='FLAG=VALUE', default=None,
                        help='write FLAG=VALUE into the isolated eval HOME config.yaml '
                             '(e.g. agent.turn_intent=true); omit for flag-off baseline')
    parser.add_argument('--ab-value-off', default='false',
                        help='value written for the flag-off variant (default false)')
    parser.add_argument('--offline', action='store_true',
                        help='deterministic loopback fake model (harness-mechanics test only)')
    parser.add_argument('--skip-gateway-check', action='store_true')
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if (home / 'workspaces').exists() or (home / 'memories').exists():
        raise SystemExit(f'refusing to reuse populated home: {home}')

    data = load_cases()
    cases = data['cases']
    if len(cases) != 30:
        raise SystemExit(f'case set drifted: {len(cases)} cases, expected 30')

    # model endpoint: default configured model via magpie, unless offline
    endpoint = os.environ.get('DL_UNDERSTAND_BASE_URL', DEFAULT_GATEWAY)
    model = os.environ.get('DL_UNDERSTAND_MODEL', DEFAULT_MODEL)
    model_endpoint = 'fake' if args.offline else 'gateway'
    gateway_ok = False
    if not args.offline and not args.skip_gateway_check:
        gateway_ok = gateway_available(endpoint)
        if not gateway_ok:
            print('gateway unreachable — falling back to deterministic offline fake; '
                  'model baseline pending', file=sys.stderr)
            model_endpoint = 'fake'

    # --ab: flag value into the ISOLATED home config (never the user's)
    ab_meta = None
    if args.ab:
        flag, _, raw_value = args.ab.partition('=')
        value = raw_value if raw_value else args.ab_value_off
        apply_ab_flag(home, flag.strip(), value)
        ab_meta = {'flag': flag.strip(), 'value': value}

    # Owner hard rule: persona MDs must be byte-identical before/after.
    # The product itself writes a default SOUL.md into a FRESH home on first
    # agent construction, so warm up once (build + close one agent) and take
    # the baseline digest AFTER product initialisation is complete.
    sys.path.insert(0, str(REPO))
    warm_agent, warm_ep, warm_db = make_agent(
        'understand-eval-warmup', model_endpoint=model_endpoint,
        model=model, endpoint=endpoint,
        api_key=os.environ.get('DL_UNDERSTAND_API_KEY', 'local-gateway'),
    )
    close_quietly(warm_agent)
    if warm_ep:
        close_endpoint(warm_ep)
    close_quietly(warm_db)
    persona_before = persona_digests(home)

    # memory corpus once per home (shared across cases; recall path reads it)
    sys.path.insert(0, str(REPO))
    write_memory_corpus(home, data['memory_corpus'])

    started = time.time()
    rows = []
    for case in cases:
        row = run_case(
            case, home,
            model_endpoint=model_endpoint, endpoint=endpoint, model=model,
            api_key=os.environ.get('DL_UNDERSTAND_API_KEY', 'local-gateway'),
            run_tag=time.strftime('%H%M%S'), offline=args.offline,
        )
        rows.append(row)

    check_persona_unchanged(persona_before, persona_digests(home))

    summary = aggregate(rows)
    result = {
        'meta': {
            'home': str(home),
            'cases': len(cases),
            'model_endpoint': model_endpoint,
            'model': model if model_endpoint == 'gateway' else 'offline-fake',
            'gateway_available': gateway_ok,
            'ab': ab_meta,
            'persona_sha256': persona_before,
            'elapsed_seconds': round(time.time() - started, 1),
            'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        },
        'summary': summary,
        'per_case': rows,
    }
    with output.open('w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps({'summary': summary, 'ab': ab_meta}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
