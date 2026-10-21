"""Interleave native/fallback samples and assert byte-for-byte output equivalence."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=11)
    args = parser.parse_args()
    if not os.environ.get('STARDUST_HOME') or args.iterations < 1:
        parser.error('isolated STARDUST_HOME and positive iterations required')
    import stardust_native as native
    import stardust_native_support as support
    from tools.fuzzy_match import find_closest_lines
    from agent.redact import redact_sensitive_text

    text = '\n'.join(f'    value_{i} = process_item("example", enabled=True)' for i in range(10000))
    cases = {
        'similar_10k': lambda: find_closest_lines('value_missing = process_item("example", enabled=False)', text),
        'redact_10k': lambda: redact_sensitive_text(text, force=True),
        'redact_with_secrets': lambda: redact_sensitive_text(text + '\nAPI_KEY=synthetic-fixture-123456789012345', force=True),
    }
    results = {}
    for name, call in cases.items():
        samples = {'python': [], 'rust': []}
        reference = None
        for i in range(args.iterations + 1):
            for backend in (('python', 'rust') if i % 2 == 0 else ('rust', 'python')):
                support.sequence_ratios = native.sequence_ratios if backend == 'rust' else None
                support.assignment_candidate = native.assignment_candidate if backend == 'rust' else None
                start = time.perf_counter()
                output = call()
                elapsed = (time.perf_counter() - start) * 1000
                if reference is None:
                    reference = output
                assert output == reference, name
                if i:
                    samples[backend].append(elapsed)
        results[name] = {key: {'median_ms': statistics.median(values), 'samples_ms': values}
                         for key, values in samples.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    for name, values in results.items():
        print(name, {key: round(value['median_ms'], 3) for key, value in values.items()})


if __name__ == '__main__':
    main()
