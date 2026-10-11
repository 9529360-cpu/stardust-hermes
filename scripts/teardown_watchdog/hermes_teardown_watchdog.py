"""pytest plugin that the per-file runner loads into each child (scripts/run_tests_parallel.py).

pytest prints its summary and then returns. A child still alive well after that is hung in
interpreter exit, which the runner otherwise reports only as a bare file timeout. This plugin
arms a one-shot faulthandler dump in pytest_unconfigure: if the process is still running
HERMES_TEST_TEARDOWN_DUMP_SECONDS later (default 90; 0 disables), every thread's stack is written.
The runner names the destination in HERMES_TEST_TEARDOWN_DUMP_FILE and reads it back after the
child exits; without that variable the dump goes to the process's original stderr.

The dump never exits the process, so the runner's timeout and failure accounting are unchanged.
A process that exits normally cancels the pending dump during interpreter finalization.
"""

from __future__ import annotations

import faulthandler
import os
import sys

import pytest

DUMP_FILE_ENV = "HERMES_TEST_TEARDOWN_DUMP_FILE"
_DEFAULT_DUMP_SECONDS = 90.0
# Keeps the dump file object alive until the timer fires (faulthandler keeps only the descriptor).
_DUMP_STREAMS: list = []


def _dump_seconds() -> float:
    raw = os.environ.get("HERMES_TEST_TEARDOWN_DUMP_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_DUMP_SECONDS
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_DUMP_SECONDS


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # trylast: pytest restores its own stderr capture in a cleanup that runs after unconfigure
    # hooks, so the fallback stream must be the process's original stderr (sys.__stderr__).
    seconds = _dump_seconds()
    if seconds <= 0:
        return
    path = os.environ.get(DUMP_FILE_ENV, "")
    if path:
        stream = open(path, "w", encoding="utf-8")
        _DUMP_STREAMS.append(stream)
    else:
        stream = sys.__stderr__
    faulthandler.dump_traceback_later(seconds, repeat=False, file=stream)
