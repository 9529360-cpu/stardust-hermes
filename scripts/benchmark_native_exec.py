"""Interleaved end-to-end executor comparison including JSONL fallback."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shlex
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=21)
    args = parser.parse_args()
    if not os.environ.get('STARDUST_HOME') or args.iterations < 1:
        parser.error('isolated STARDUST_HOME and positive iterations required')
    native = importlib.import_module('stardust_native')
    from tools.environments.local import LocalEnvironment

    python = shlex.quote(sys.executable.replace('\\', '/'))
    cases = {'echo': ('printf hello', 'hello'),
             'large_output': (python + ' -c ' + shlex.quote("print('hi\\n' * 350000, end='')"),
                              ('hi\r\n' if os.name == 'nt' else 'hi\n') * 350000)}
    env = LocalEnvironment(cwd=str(Path.cwd()))
    results = {}
    original = os.environ.get('STARDUST_EXEC_BACKEND')
    try:
        env.execute('true')
        for name, (command, expected) in cases.items():
            samples = {key: [] for key in ('python', 'helper', 'native')}
            for i in range(args.iterations + 1):
                keys = list(samples)
                keys = keys[i % 3:] + keys[:i % 3]
                for backend in keys:
                    os.environ['STARDUST_EXEC_BACKEND'] = 'python' if backend == 'python' else 'rust'
                    sys.modules['stardust_native'] = None if backend == 'helper' else native
                    start = time.perf_counter()
                    result = env.execute(command)
                    elapsed = (time.perf_counter() - start) * 1000
                    assert result['returncode'] == 0
                    assert result['output'] == expected, (backend, name, len(result['output']))
                    if i:
                        samples[backend].append(elapsed)
            results[name] = {key: {'median_ms': statistics.median(value), 'samples_ms': value}
                             for key, value in samples.items()}
    finally:
        sys.modules['stardust_native'] = native
        if original is None:
            os.environ.pop('STARDUST_EXEC_BACKEND', None)
        else:
            os.environ['STARDUST_EXEC_BACKEND'] = original
        env.cleanup()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    for name, values in results.items():
        print(name, {key: round(value['median_ms'], 3) for key, value in values.items()})


if __name__ == '__main__':
    main()
