"""Kill/restart measurement through real AIAgent + loopback HTTP + real file tools.

Run with an explicit isolated six-variable HOME; see README.md. No product hooks.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

HOME_VARS = ('HOME', 'USERPROFILE', 'STARDUST_HOME', 'HERMES_HOME', 'TEMP', 'TMP')
POINTS = ('before_tool', 'after_effect_before_commit', 'after_commit', 'before_final')
FILES = {'a.txt': 'alpha\n', 'b.txt': 'beta\n', 'c.txt': 'gamma\n',
         'summary.txt': 'a.txt alpha; b.txt beta; c.txt gamma\n'}
REPO = Path(__file__).resolve().parents[2]


def assert_home(home, env=None):
    """Validate before importing product modules (which can initialize state)."""
    env = os.environ if env is None else env
    home = Path(home).resolve()
    forbidden = Path('D:/Stardust/home').resolve()
    for key in HOME_VARS:
        if not env.get(key):
            raise ValueError(f'{key} must be explicitly isolated')
        actual = Path(env[key]).resolve()
        if actual == forbidden or forbidden in actual.parents or actual != home:
            raise ValueError(f'unsafe or mismatched {key}')
    if home == REPO or REPO in home.parents:
        raise ValueError('HOME must be outside the source tree')
    return home


def child_environment(home):
    # Do not inherit model credentials, routing, plugins, or external login stores.
    keep = ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
            'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'STARDUST_GIT_BASH_PATH')
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({k: str(home) for k in HOME_VARS})
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               PYTHONIOENCODING='utf-8', HERMES_PROFILE='default',
               APPDATA=str(home / 'appdata'), LOCALAPPDATA=str(home / 'localappdata'),
               XDG_CACHE_HOME=str(home / 'cache'), GH_CONFIG_DIR=str(home / 'gh'),
               COPILOT_GITHUB_TOKEN='offline-eval-no-copilot')
    assert_home(home, env)
    return env


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def pause_at(home, point):
    dump(home / 'ready.json', {'point': point, 'pid': os.getpid()})
    # Parent kills only this Popen PID. Bounded fallback prevents orphaned experiments.
    threading.Event().wait(120)
    raise TimeoutError('parent did not kill at barrier')


def child(home, mode, point):
    assert_home(home)
    os.chdir(home)
    # Audit Python filesystem writes before importing AIAgent. Kernel/subprocess writes are
    # additionally constrained by the fixed write_file toolset and six-variable environment.
    def audit(event, args):
        target = None
        if event == 'open':
            path, mode_arg, flags = args
            if isinstance(path, (str, bytes, os.PathLike)) and (
                (isinstance(mode_arg, str) and any(c in mode_arg for c in 'wax+'))
                or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                target = path
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir'):
            target = args[0]
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            for path in args[:2]:
                resolved = Path(os.fsdecode(path)).resolve()
                if resolved != home and home not in resolved.parents:
                    raise PermissionError('eval filesystem boundary')
        if target is not None:
            # subprocess.DEVNULL is a kernel sink, not a filesystem side effect.
            if os.fsdecode(target).lower() in ('nul', '/dev/null'):
                return
            resolved = Path(os.fsdecode(target)).resolve()
            if resolved != home and home not in resolved.parents:
                with (home / 'denied-writes.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'event': event, 'path': str(resolved)}) + '\n')
                raise PermissionError('eval filesystem boundary')
    sys.addaudithook(audit)

    from evals.assistant_scenarios.fake_model import FakeModel, Reply
    from hermes_state import SessionDB
    from run_agent import AIAgent
    import model_tools

    def script(request):
        # Decide solely from durable conversation, never inspect target files to rescue the
        # agent. An effect without a committed result will therefore be offered again.
        committed = set()
        for message in request.messages:
            if message.get('role') == 'tool':
                # Match successful results against their paired assistant call.
                call_id = message.get('tool_call_id')
                if '"error"' in str(message.get('content', '')):
                    continue
                for prior in request.messages:
                    for tc in prior.get('tool_calls') or []:
                        if tc.get('id') == call_id and tc['function']['name'] == 'write_file':
                            committed.add(Path(json.loads(tc['function']['arguments'])['path']).name)
        last = request.messages[-1] if request.messages else {}
        if last.get('role') == 'tool' and 'Refusing to overwrite' in str(last.get('content', '')):
            for prior in reversed(request.messages):
                for tc in prior.get('tool_calls') or []:
                    if tc.get('id') == last.get('tool_call_id'):
                        return Reply(tool_calls=[('read_file', {'path': json.loads(tc['function']['arguments'])['path']})])
        for name, content in FILES.items():
            if name not in committed:
                return Reply(tool_calls=[('write_file', {'path': str(home / name), 'content': content})])
        if mode == 'initial' and point == 'before_final':
            pause_at(home, point)
        return Reply(text='RECOVERY_TASK_COMPLETE')

    original = model_tools.handle_function_call

    def dispatch(name, args, *a, **kw):
        result = original(name, args, *a, **kw)
        if name == 'write_file':
            parsed = json.loads(args) if isinstance(args, str) else args
            path = Path(parsed['path'])
            if (path.is_file() and path.read_text(encoding='utf-8') == parsed['content']
                    and isinstance(result, str) and '"verified": true' in result):
                with (home / 'effects.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'file': path.name, 'mode': mode}) + '\n')
                if mode == 'initial' and point == 'after_effect_before_commit' and path.name == 'b.txt':
                    pause_at(home, point)
        return result

    model_tools.handle_function_call = dispatch

    def start(_id, name, args):
        if mode == 'initial' and point == 'before_tool' and name == 'write_file' and Path(args['path']).name == 'b.txt':
            pause_at(home, point)

    def complete(_id, name, args, result):
        if mode == 'initial' and point == 'after_commit' and name == 'write_file' and Path(args['path']).name == 'b.txt':
            pause_at(home, point)

    with FakeModel(script) as endpoint, SessionDB() as db:
        agent = AIAgent(base_url=endpoint.base_url, api_key='offline-eval', provider='custom',
                        api_mode='chat_completions', model='recovery-local', session_id='recovery',
                        session_db=db, enabled_toolsets=['file'], max_iterations=12,
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        skip_background_review=True, tool_start_callback=start,
                        tool_complete_callback=complete)
        dump(home / 'boot.json', {'pid': os.getpid(), 'mode': mode})
        if mode == 'restart':
            # AIAgent is turn-driven: do not turn a restart into implicit user authorization.
            # Keep this real instance alive and observe idle before sending actual input.
            command = sys.stdin.readline().strip()
            if command != 'continue':
                raise ValueError('restart requires literal continue input')
            history = db.get_messages('recovery')
        else:
            command = 'Write a.txt, b.txt, c.txt, then summary.txt with the specified contents.'
            history = None
        started = time.monotonic()
        result = agent.run_conversation(command, conversation_history=history)
        dump(home / 'finished.json', {'final_response': result.get('final_response'),
                                     'seconds': time.monotonic() - started})
        dump(home / 'state.json', {
            'checkpoint': db.get_session_model_config_value('recovery', '_task_checkpoint_v1'),
            'message_count': len(db.get_messages('recovery'))})
        agent.close()


def wait_file(path, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        if process.poll() is not None:
            raise RuntimeError(f'child exited {process.returncode}; inspect {path.parent}/child.log')
        time.sleep(.05)
    raise TimeoutError(f'timed out waiting for {path.name}')


def snapshot(home):
    files = {name: {'present': (home / name).is_file(),
                    'correct': (home / name).is_file() and (home / name).read_text(encoding='utf-8') == content}
             for name, content in FILES.items()}
    effects_path = home / 'effects.jsonl'
    effects = [json.loads(s) for s in effects_path.read_text(encoding='utf-8').splitlines()] if effects_path.exists() else []
    counts = collections.Counter(e['file'] for e in effects)
    duplicates = sum(max(0, count - 1) for count in counts.values())
    finished = home / 'finished.json'
    answer = json.loads(finished.read_text(encoding='utf-8')) if finished.exists() else None
    return {'files': files, 'duplicate_side_effects': duplicates, 'effects': effects,
            'completed': all(f['correct'] for f in files.values()) and bool(answer and answer['final_response'] == 'RECOVERY_TASK_COMPLETE'),
            'answer': answer}


def durable_state(home):
    import sqlite3
    path = home / 'state.db'
    if not path.exists():
        return {'checkpoint': None, 'life_events': []}
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        row = db.execute("SELECT model_config FROM sessions WHERE id='recovery'").fetchone()
    events = []
    ledger = home / 'life_events.db'
    if ledger.exists():
        with sqlite3.connect(f'{ledger.as_uri()}?mode=ro', uri=True) as db:
            events = [json.loads(r[0]) for r in db.execute('SELECT record FROM life_events ORDER BY timestamp_ns')]
    return {'checkpoint': json.loads(row[0] or '{}').get('_task_checkpoint_v1') if row else None,
            'life_events': events}


def run_case(root, index, point, idle_seconds=30):
    home = root / f'case-{index:02d}-{point}'
    home.mkdir(parents=True, exist_ok=False)
    env = child_environment(home)
    row = {'run': index, 'kill_point': point, 'verdict': 'harness_error'}
    processes = []
    def launch(mode, log):
        # Windows venv python.exe can be a redirector with a different child PID.
        # Launch the actual interpreter, retaining this environment's dependency paths.
        bootstrap = ('import sys,runpy; sys.path[:0]=' + repr(sys.path) +
                     '; runpy.run_module("evals.digital_life.recovery",run_name="__main__")')
        process = subprocess.Popen([sys._base_executable, '-B', '-c', bootstrap,
                                    '--child', mode, '--home', str(home), '--point', point],
                                   cwd=home, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
        processes.append(process)
        return process
    try:
        with (home / 'child.log').open('w', encoding='utf-8') as log:
            initial = launch('initial', log)
            ready = wait_file(home / 'ready.json', initial)
            if ready != {'point': point, 'pid': initial.pid}:
                raise RuntimeError('kill barrier identity mismatch')
            initial.kill()
            initial.wait(timeout=15)
            row.update(killed_pid=initial.pid, kill_exit=initial.returncode,
                       interrupted=snapshot(home), interrupted_state=durable_state(home))
            (home / 'boot.json').unlink(missing_ok=True)
            restart = launch('restart', log)
            wait_file(home / 'boot.json', restart)
            started = time.monotonic()
            deadline = started + idle_seconds
            while time.monotonic() < deadline:
                if restart.poll() is not None:
                    raise RuntimeError('restart exited while idle')
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
            row['auto'] = dict(snapshot(home), observed_seconds=time.monotonic() - started)
            row['auto_state'] = durable_state(home)
            started = time.monotonic()
            restart.stdin.write('continue\n')
            restart.stdin.flush()
            restart.wait(timeout=90)
            row['continue'] = dict(snapshot(home), seconds_to_completion=None,
                                   seconds_to_verdict=time.monotonic() - started,
                                   exit_code=restart.returncode)
            if row['continue']['completed']:
                row['continue']['seconds_to_completion'] = row['continue']['seconds_to_verdict']
            row['final_state'] = durable_state(home)
            row['denied_writes'] = (home / 'denied-writes.jsonl').read_text(encoding='utf-8').splitlines() if (home / 'denied-writes.jsonl').exists() else []
            row['verdict'] = 'recorded' if restart.returncode == 0 else 'child_error'
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
            if process.stdin:
                process.stdin.close()
    dump(home / 'result.json', row)
    return row


def report(rows):
    return {'runs': len(rows), 'recorded': sum(r['verdict'] == 'recorded' for r in rows),
            'auto_continue': {'completed': sum(r.get('auto', {}).get('completed', False) for r in rows), 'denominator': len(rows)},
            'continue_command': {'completed': sum(r.get('continue', {}).get('completed', False) for r in rows), 'denominator': len(rows)},
            'duplicate_side_effects': sum(r.get('continue', {}).get('duplicate_side_effects', 0) for r in rows),
            'denied_writes': sum(len(r.get('denied_writes', [])) for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--child', choices=['initial', 'restart'])
    parser.add_argument('--point', choices=POINTS)
    args = parser.parse_args()
    home = assert_home(args.home)
    if args.child:
        child(home, args.child, args.point)
        return
    if not 1 <= args.runs <= 10 or args.output is None:
        parser.error('runs must be 1..10 and --output is required')
    home.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(home).free < 2 * 1024**3:
        raise RuntimeError('at least 2 GiB free required')
    rows = []
    for i in range(args.runs):
        if sum(p.stat().st_size for p in home.rglob('*') if p.is_file()) > 200 * 1024**2:
            raise RuntimeError('200 MiB artifact limit exceeded')
        rows.append(run_case(home, i + 1, POINTS[i % len(POINTS)]))
        result = {'schema_version': 1, 'flags': 'product defaults; no new flag',
                  'scope': 'AIAgent host; idle stdin restart, not full CLI/gateway startup',
                  'summary': report(rows), 'runs': rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        dump(args.output, result)
        table = ['| Run | Kill point | Verdict | Auto (30s) | Continue | Duplicates |',
                 '|---|---|---|---|---|---|']
        table += [f"| {r['run']} | {r['kill_point']} | {r['verdict']} | {r.get('auto', {}).get('completed')} | {r.get('continue', {}).get('completed')} | {r.get('continue', {}).get('duplicate_side_effects')} |" for r in rows]
        args.output.with_suffix('.md').write_text('\n'.join(table) + '\n\n' + json.dumps(result['summary']), encoding='utf-8')
        print(json.dumps({'run': i + 1, **report(rows)}), flush=True)
    if report(rows)['recorded'] != args.runs:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
