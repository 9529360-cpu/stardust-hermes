"""Context-hash capture for the DL-P2.6 harness (DL-P2.7/P2.9 method).

Replays the fixed 100-question case set through the production recall path
(``_memory_turn_start_and_prefetch``) in an isolated HOME and records the
SHA-256 of the injected sidecar context per question. Comparing two captures
(orig-main tree vs candidate tree with the flag off) proves byte-identical
recall output.

Run from the repo root of the tree under test with its venv python:
  python evals/digital_life/context_hash_capture.py --home HOME --output HASHES.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from evals.digital_life.memory_retrieval import (
    HOME_VARS, assert_home, load_cases, make_agent, close_endpoint, close_quietly,
)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)

    # Corpus must exist in this home (written by memory_retrieval.write_corpus).
    cases = load_cases()
    questions = cases['questions']
    from agent.turn_context import _memory_turn_start_and_prefetch

    hashes: dict[str, str] = {}
    contexts: dict[str, str] = {}
    for index, q in enumerate(questions):
        agent, endpoint, db = make_agent(f'hash-q{index:03d}')
        try:
            context = _memory_turn_start_and_prefetch(agent, q['question'])
        finally:
            close_quietly(agent)
            close_endpoint(endpoint)
            close_quietly(db)
        text = context or ''
        hashes[q['id']] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        contexts[q['id']] = text

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(hashes, ensure_ascii=False, indent=1), encoding='utf-8')
    sidecar = args.output.with_suffix('.contexts.json')
    sidecar.write_text(json.dumps(contexts, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'captured {len(hashes)} context hashes -> {args.output}')


if __name__ == '__main__':
    main()
