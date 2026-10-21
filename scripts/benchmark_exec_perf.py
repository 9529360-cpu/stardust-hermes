"""Reproducible local execution benchmark; run with an isolated STARDUST_HOME."""
import argparse
import cProfile
import json
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--baseline-ref', help='Load base_output.py from this Git ref before measuring')
    args = parser.parse_args()
    if not os.environ.get('STARDUST_HOME'):
        parser.error('set STARDUST_HOME to an isolated task directory')
    if args.iterations < 1:
        parser.error('--iterations must be positive')
    if args.baseline_ref:
        # Execute the exact committed module without modifying the working tree.
        import types

        name = 'tools.environments.base_output'
        source = subprocess.check_output(
            ['git', 'show', f'{args.baseline_ref}:tools/environments/base_output.py'])
        module = types.ModuleType(name)
        module.__file__ = str(Path(__file__).resolve().parents[1] / 'tools/environments/base_output.py')
        sys.modules[name] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    from tools.environments.local import LocalEnvironment
    from tools.environments.base_output import _ConsoleCodePageFallbackDecoder

    args.output.parent.mkdir(parents=True, exist_ok=True)
    env = LocalEnvironment(cwd=str(Path.cwd()))
    results = {
        'metadata': {
            'python': sys.version,
            'platform': sys.platform,
            'baseline_ref': args.baseline_ref,
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        },
    }
    profiler = cProfile.Profile()
    python = shlex.quote(sys.executable.replace('\\', '/'))
    timeout_pids = args.output.resolve().with_suffix('.pids.json')
    timeout_code = (
        'import json, os, pathlib, subprocess, sys, time; '
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
        f'pathlib.Path({timeout_pids.as_posix()!r}).write_text(json.dumps([os.getpid(), child.pid])); '
        'time.sleep(30)'
    )
    cases = {
        'echo': ('echo hi', args.iterations, 30, 0),
        'large_output': (python + ' -c ' + shlex.quote("print('hi\\n' * 350000, end='')"), 5, 30, 0),
        'timeout': (python + ' -c ' + shlex.quote(timeout_code), 3, 0.25, 124),
    }
    try:
        env.execute('echo warmup')
        profiler.enable()
        for name, (command, count, timeout, expected) in cases.items():
            elapsed = []
            for _ in range(count):
                start = time.perf_counter()
                result = env.execute(command, timeout=timeout)
                elapsed.append(time.perf_counter() - start)
                assert result['returncode'] == expected, result
                if name == 'echo':
                    assert result['output'].strip() == 'hi', result
                elif name == 'large_output':
                    assert result['output'].splitlines() == ['hi'] * 350000
                elif name == 'timeout':
                    import psutil

                    pids = json.loads(timeout_pids.read_text())
                    assert all(not psutil.pid_exists(pid) for pid in pids), pids
                    timeout_pids.unlink()
            results[name] = {'n': count, 'median_ms': statistics.median(elapsed) * 1000,
                             'samples_ms': [s * 1000 for s in elapsed]}
        profiler.disable()
        elapsed = []
        for _ in range(7):
            decoder = _ConsoleCodePageFallbackDecoder('cp437')
            chunk = b'hi\n' * 1365
            start = time.perf_counter()
            for _ in range(2000):
                assert decoder.decode(chunk) == chunk.decode('ascii')
            elapsed.append(time.perf_counter() - start)
        results['ascii_decoder_8MB'] = {'n': 7, 'median_ms': statistics.median(elapsed) * 1000,
                                      'samples_ms': [s * 1000 for s in elapsed]}
    finally:
        env.cleanup()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(results, indent=2))
    if args.profile:
        profiler.dump_stats(str(args.profile))


if __name__ == '__main__':
    main()
