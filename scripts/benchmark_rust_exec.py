"""Compare real LocalEnvironment execution; run with an isolated STARDUST_HOME."""
import argparse
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
    parser.add_argument('--iterations', type=int, default=20)
    args = parser.parse_args()
    if not os.environ.get('STARDUST_HOME') or args.iterations < 1:
        parser.error('isolated STARDUST_HOME and positive iterations required')
    from tools.environments.local import LocalEnvironment
    python = shlex.quote(sys.executable.replace('\\', '/'))
    cases = {
        'echo': ('printf hello', 'hello'),
        'large_output': (python + ' -c ' + shlex.quote("print('hi\\n' * 350000, end='')"), ('hi\r\n' if os.name == 'nt' else 'hi\n') * 350000),
    }
    results = {'python_version': sys.version, 'platform': sys.platform, 'samples': {}}
    original = os.environ.get('STARDUST_EXEC_BACKEND')
    try:
        for backend in ('python', 'rust'):
            os.environ['STARDUST_EXEC_BACKEND'] = backend
            env = LocalEnvironment(cwd=str(Path.cwd()))
            try:
                env.execute('true')
                results['samples'][backend] = {}
                for name, (command, expected) in cases.items():
                    samples = []
                    for _ in range(args.iterations):
                        start = time.perf_counter()
                        result = env.execute(command)
                        samples.append((time.perf_counter() - start) * 1000)
                        assert result['returncode'] == 0, result
                        assert result['output'] == expected, (name, len(result['output']))
                    results['samples'][backend][name] = {
                        'median_ms': statistics.median(samples), 'samples_ms': samples}
            finally:
                env.cleanup()
    finally:
        if original is None:
            os.environ.pop('STARDUST_EXEC_BACKEND', None)
        else:
            os.environ['STARDUST_EXEC_BACKEND'] = original
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
