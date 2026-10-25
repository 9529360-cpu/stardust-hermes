"""Budgeted paired understand-user eval; subprocess isolation per arm/case.

Each arm reserves four main requests + one paraphrase judge (+ one intent
sidecar if enabled). Title, background review, compression and exhaustion
summary are disabled identically. Incomplete answers count as failed rows.
Pairs reserve their worst-case cost before either arm starts, then release
unused capacity. Transport failures invalidate the run; wire caps include retries.
"""
from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from evals.digital_life import understand_user as uu

CANDIDATE = {
    'terminal.cwd_env_policy': ('env_first', 'session_first'),
    'tools.file_search_session_anchor': (False, True),
    'memory.hybrid_recall': (False, True),
    'memory.topic_provenance_enabled': (False, True),
    'tool_loop_guardrails.identical_failure_cap': (False, True),
    'tool_loop_guardrails.identical_failure_cap_shadow': (False, False),
}
ALL_FLAGS = (
    'digital_life.recovery_enabled', 'digital_life.auto_resume',
    'agent.artifact_acceptance', 'agent.capability_refusal_guard',
    'agent.turn_intent', 'agent.turn_intent_llm', 'agent.verify_on_stop_messaging',
    'voice.barge_in_owner_only', 'connectors.oauth_secure_store',
    'connectors.unified_dialog', 'connector_secret_store.enabled',
    'tui_gateway.rpc_admission.enabled', 'dashboard.compute_host_epoch_guard',
    'memory.fact_pipeline', 'memory.semantic_recall',
)
ENV_FLAGS = ('STARDUST_NATIVE_FILE_READ', 'STARDUST_DEFER_CONTEXT_PROBE')


def set_path(config, key, value):
    parts = key.split('.')
    for part in parts[:-1]:
        config = config.setdefault(part, {})
    config[parts[-1]] = value


def flag_config(arm, profile):
    config = {}
    for key, values in CANDIDATE.items():
        set_path(config, key, values[arm == 'on'])
    if profile == 'all':
        for key in ALL_FLAGS:
            set_path(config, key, arm == 'on')
    return config


def case_cost(config):
    return uu.MAX_AGENT_API_CALLS + 1 + int(bool(config.get('agent', {}).get('turn_intent_llm')))


def run_pairs(cases, run, *, cap, configs=None):
    configs = configs or {'off': {}, 'on': {}}
    costs = {arm: case_cost(configs[arm]) for arm in ('off', 'on')}
    result = {'pairs': [], 'reserved': 0, 'consumed': 0, 'per_arm_cost': costs, 'stop_reason': 'complete'}
    for case in cases:
        cost = sum(costs.values())
        if result['consumed'] + cost > cap:
            result['stop_reason'] = 'budget'
            break
        result['reserved'] += cost
        pair = {'id': case['id'], 'reservation': cost}
        try:
            for arm in ('off', 'on'):
                row = run(case, arm, costs[arm])
                if row.get('run_completed') is False:
                    row['passed'] = False
                    row['failure_reason'] = row.get('turn_exit_reason') or 'agent incomplete'
                pair[arm] = row
                result['consumed'] += row.get('inference_posts', costs[arm])
        except Exception as exc:
            result.update(stop_reason=type(exc).__name__, incomplete={'id': case['id']})
            break
        result['pairs'].append(pair)
    return result


def is_inference(method, path):
    return method == 'POST' and urlsplit(path).path.rstrip('/').endswith(
        ('/chat/completions', '/responses', '/messages'))


class CountingRelay:
    """Loopback-only relay: log method/path/status, never headers or bodies."""
    def __init__(self, cap, *, upstream_port=3425):
        self.cap = cap
        self.calls = []
        self.forwarded = 0
        self.allowance = 0
        self.arm_calls = 0
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def relay(self):
                inference = is_inference(self.command, self.path)
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                with owner.lock:
                    if inference and (owner.stopped.is_set() or owner.forwarded >= owner.cap
                                      or owner.arm_calls >= owner.allowance):
                        owner.stopped.set()
                        owner.calls.append({'method': self.command, 'path': urlsplit(self.path).path,
                                            'status': 400, 'forwarded': False})
                        self.send_error(400, 'eval reservation exhausted')
                        return
                    if inference:
                        owner.forwarded += 1
                        owner.arm_calls += 1
                conn = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=120)
                try:
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in ('host', 'connection', 'transfer-encoding')}
                    conn.request(self.command, self.path, body, headers)
                    response = conn.getresponse()
                    with owner.lock:
                        owner.calls.append({'method': self.command, 'path': urlsplit(self.path).path,
                                            'status': response.status, 'forwarded': True})
                        if inference and response.status >= 400:
                            owner.stopped.set()
                    # Do not expose retryable inference failures to the SDK;
                    # metadata discovery preserves the upstream status.
                    self.send_response(400 if inference and response.status >= 400
                                       else response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                            self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    while chunk := response.read1(8192):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    owner.stopped.set()
                finally:
                    conn.close()
                    self.close_connection = True

            do_GET = do_POST = relay

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def bind_fixture(agent, case, user_turn):
    """File-only contract v2: expose fixture paths, never host process tools.

    Four calls allow search, read, and a normal final answer.
    The old unrestricted terminal/discovery surface spent those calls inspecting
    the host or discovering tools; its finalizer then made an unplanned summary.
    This contract changes task setup, never expected constraints or score rules.
    """
    allowed = set(case.get('allowed_tools', ('read_file', 'search_files')))
    agent.tools = [t for t in agent.tools if t['function']['name'] in allowed]
    agent.valid_tool_names = {t['function']['name'] for t in agent.tools}
    agent._handle_max_iterations = lambda messages, count: ''
    paths = ', '.join(sorted(case.get('files', {}))) or '(empty)'
    return ('评测环境：这是隔离的文件工作区，环境状态只由以下 fixture 文件表示；'
            '本项评测只要求执行一个首动作后汇报已观察结果及后续计划，不要求完成整项任务。'
            '可用工具仅为读取/搜索（写入案例可显式声明写工具），不访问宿主进程、网络或外部代理。'
            '文件清单：' + paths + '\n\n' + user_turn)


def worker(home, case_id, output):
    uu.assert_home(home)
    # Import only after all HOME variables have been validated for this worker.
    from hermes_cli.config import load_config_readonly
    config = load_config_readonly()
    route = config['model']
    data = uu.load_cases()
    case = next(c for c in data['cases'] if c['id'] == case_id)
    uu.write_memory_corpus(home, data['memory_corpus'])
    # Initialisation may create the stock SOUL; no MD is written by this harness.
    agent, ep, db = uu.make_agent('paired-warmup', model_endpoint='gateway',
        model=route['default'], endpoint=route['base_url'], api_key='local-gateway')
    uu.close_quietly(agent)
    uu.close_quietly(db)
    before = uu.persona_digests(home)
    row = uu.run_case(case, home, model_endpoint='gateway', endpoint=route['base_url'],
                      model=route['default'], api_key='local-gateway', run_tag='paired',
                      offline=False, fixture_contract=True)
    after = uu.persona_digests(home)
    uu.check_persona_unchanged(before, after)
    row['persona_before'] = before
    row['persona_after'] = after
    output.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cap', type=int, default=30)
    parser.add_argument('--cases', type=int, default=5)
    parser.add_argument('--profile', choices=('candidate', 'all'), default='candidate')
    parser.add_argument('--route-config', type=Path)
    parser.add_argument('--worker')
    args = parser.parse_args(argv)
    if args.worker:
        worker(args.home, args.worker, args.output)
        return 0
    import yaml
    home = args.home.resolve()
    if home.exists():
        raise SystemExit('refusing to reuse paired home')
    if home == uu.FORBIDDEN_HOME or uu.FORBIDDEN_HOME in home.parents:
        raise SystemExit('unsafe paired home')
    home.mkdir(parents=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = yaml.safe_load(args.route_config.read_text(encoding='utf-8'))
    # Copy only non-secret model route selectors from production config.
    route = {k: source['model'][k] for k in ('default', 'provider', 'api_mode')
             if k in source['model']}
    configs = {arm: flag_config(arm, args.profile) for arm in ('off', 'on')}
    cases = uu.load_cases()['cases'][:args.cases]
    if args.cap < 0 or not 1 <= args.cases <= len(uu.load_cases()['cases']):
        raise SystemExit('cap must be non-negative and cases must be between 1 and 30')
    with CountingRelay(args.cap) as relay:
        endpoint = f'http://127.0.0.1:{relay.server.server_port}/v1'

        def run(case, arm, reservation):
            case_home = home / f'{case["id"]}-{arm}'
            case_home.mkdir()
            cfg = copy.deepcopy(configs[arm])
            cfg['model'] = dict(route, provider='custom', base_url=endpoint)
            cfg['auxiliary'] = {'title_generation': {'enabled': False},
                                'background_review': {'enabled': False}}
            cfg['compression'] = {'enabled': False, 'micro_compaction': {'enabled': False}}
            cfg['smart_model_routing'] = {'enabled': False}
            (case_home / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
            env = {k: v for k, v in os.environ.items()
                   if not any(s in k for s in ('API_KEY', 'API_TOKEN', 'AUTH_TOKEN'))
                   and not k.startswith('STARDUST_INFERENCE_')}
            for key in uu.HOME_VARS:
                env[key] = str(case_home)
            for key in ENV_FLAGS:
                env[key] = '1' if arm == 'on' else '0'
            env['STARDUST_COMPUTE_HOST_EPOCH_GUARD'] = str(int(arm == 'on' and args.profile == 'all'))
            env.update(PYTHONPATH=str(uu.REPO), PYTHONDONTWRITEBYTECODE='1',
                       OPENAI_API_KEY='local-gateway', OPENAI_BASE_URL=endpoint)
            row_path = case_home / 'row.json'
            relay.allowance, relay.arm_calls = reservation, 0
            failure_reason = None
            with (case_home / 'worker.log').open('w', encoding='utf-8') as log:
                proc = subprocess.Popen([sys.executable, '-B', '-m',
                    'evals.digital_life.understand_user_paired', '--home',
                    str(case_home), '--output', str(row_path), '--worker', case['id']],
                    env=env, cwd=home, stdout=log, stderr=log)
                try:
                    for _ in range(600):
                        try:
                            proc.wait(timeout=0.5)
                            break
                        except subprocess.TimeoutExpired:
                            if relay.stopped.is_set():
                                raise RuntimeError('inference transport stopped')
                    else:
                        failure_reason = 'case exceeded 300 seconds'
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=10)
            if relay.stopped.is_set():
                raise RuntimeError('inference transport stopped')
            if failure_reason or proc.returncode or not row_path.exists():
                row = uu.score_case(case, [], '')
                row.update(run_completed=False, turn_exit_reason=failure_reason or
                           f'worker exit {proc.returncode}; no completed row')
            else:
                row = json.loads(row_path.read_text(encoding='utf-8'))
            row['inference_posts'] = relay.arm_calls
            print(json.dumps({'case': case['id'], 'arm': arm, 'reserved': reservation,
                              'actual': relay.arm_calls, 'passed': row['passed']}), flush=True)
            return row

        result = run_pairs(cases, run, cap=args.cap, configs=configs)
        result.update(cap=args.cap, inference_posts=relay.forwarded, transport=relay.calls,
                      model=route, profile=args.profile, configs=configs,
                      fixture_contract='file-only-v2', requested_pairs=args.cases)
        result['summary'] = {arm: uu.aggregate([p[arm] for p in result['pairs']])
                             for arm in ('off', 'on')}
        result['valid'] = len(result['pairs']) == args.cases and not relay.stopped.is_set()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
