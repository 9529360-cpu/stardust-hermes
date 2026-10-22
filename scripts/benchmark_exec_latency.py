"""Real LocalEnvironment latency: default Python versus opt-in Rust helper.

Run before and after with the same interpreter, machine and release profile:
  python scripts/benchmark_exec_latency.py --phase before --output results.json
Requires isolated STARDUST_HOME/HERMES_HOME and TEMP/TMP, and a built helper.
Only compact timings/statuses are persisted; command output and env are not saved.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shlex
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=200)
    args = parser.parse_args()
    if args.iterations < 200:
        parser.error('at least 200 repetitions required')
    if not all(os.environ.get(k) for k in ('STARDUST_HOME', 'HERMES_HOME', 'TEMP', 'TMP')):
        parser.error('explicit isolated homes and temp directories required')
    if importlib.util.find_spec('stardust_native') is not None:
        parser.error('use an interpreter without stardust_native to measure the helper')
    from tools.environments.local import LocalEnvironment
    py = shlex.quote(sys.executable.replace('\\', '/'))
    payload = 'x' * (3 * 1024 * 1024)
    cases = {
        'echo': ('echo latency-ok', 10, 'latency-ok\n', 0),
        'powershell': ('powershell.exe -NoLogo -NoProfile -NonInteractive -Command ' + shlex.quote("[Console]::Write('ps-ok')"), 10, 'ps-ok', 0),
        'large_output': (py + ' -c ' + shlex.quote("import sys; sys.stdout.write('x' * (3 * 1024 * 1024))"), 10, payload, 0),
        'timeout': (py + ' -c ' + shlex.quote('import time; time.sleep(10)'), 0.2, None, 124),
    }
    document = json.loads(args.output.read_text()) if args.output.exists() else {
        'platform': platform.platform(), 'python': platform.python_version(),
        'iterations': args.iterations, 'measurement': 'LocalEnvironment.execute wall ms; session initialized outside timing; alternating backend order; p95 nearest rank; timeout expected=124; 3 MiB exact output',
        'phases': {},
    }
    if args.phase in document['phases']:
        parser.error('phase already exists; preserve original measurements')
    results = {b: {c: {'ms': [], 'errors': []} for c in cases} for b in ('python', 'rust')}
    original = os.environ.get('STARDUST_EXEC_BACKEND')
    environments = {}
    def select(backend):
        if backend == 'python':
            os.environ.pop('STARDUST_EXEC_BACKEND', None)
        else:
            os.environ['STARDUST_EXEC_BACKEND'] = 'rust'
    try:
        for backend in results:
            select(backend)
            environments[backend] = LocalEnvironment(cwd=str(ROOT))
            warm = environments[backend].execute('echo warmup')
            if warm['returncode'] != 0:
                raise RuntimeError(f'{backend} initialization failed')
        for name, (command, timeout, expected, code) in cases.items():
            for i in range(args.iterations):
                for backend in (('python', 'rust') if i % 2 == 0 else ('rust', 'python')):
                    select(backend)
                    start = time.perf_counter()
                    error = None
                    try:
                        result = environments[backend].execute(command, timeout=timeout)
                        if result['returncode'] != code:
                            error = f"returncode={result['returncode']} expected={code}"
                        elif expected is not None and result['output'].replace('\r\n', '\n') != expected:
                            error = f"output mismatch length={len(result['output'])}"
                    except Exception as exc:
                        error = type(exc).__name__
                    sample = results[backend][name]
                    sample['ms'].append(round((time.perf_counter() - start) * 1000, 3))
                    if error:
                        sample['errors'].append([i, error])
                if (i + 1) % 25 == 0:
                    print(f'{args.phase} {name}: {i + 1}/{args.iterations} per backend', flush=True)
            for backend in results:
                sample = results[backend][name]
                sample.update(median_ms=statistics.median(sample['ms']),
                              p95_ms=sorted(sample['ms'])[math.ceil(len(sample['ms']) * .95) - 1],
                              failures=len(sample['errors']))
                print(backend, name, {k: v for k, v in sample.items() if k not in ('ms', 'errors')}, flush=True)
            document['phases'][args.phase] = results
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(document, separators=(',', ':')) + '\n', encoding='utf-8')
    finally:
        for backend, env in environments.items():
            select(backend)
            env.cleanup()
        if original is None:
            os.environ.pop('STARDUST_EXEC_BACKEND', None)
        else:
            os.environ['STARDUST_EXEC_BACKEND'] = original


if __name__ == '__main__':
    main()
