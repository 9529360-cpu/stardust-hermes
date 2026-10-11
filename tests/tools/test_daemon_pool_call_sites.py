"""Process-level regression tests: the call sites that run best-effort work on a
stdlib ThreadPoolExecutor must not hold interpreter exit.

CPython joins every stdlib executor worker at interpreter exit (the hook in
``concurrent.futures.thread``), even after ``shutdown(wait=False)``. A caller
that gives up on a stuck worker therefore still waits for that worker before
the process can exit, for as long as the worker runs. ``tools.daemon_pool``
provides daemon workers that the join does not see.

Each test runs one production call site in a child interpreter, leaves the
call's worker stuck for ``STUCK_SECONDS`` after the caller has returned, and
measures how long the process takes to exit once its main thread is done.
With a stdlib pool the exit waits for the stuck worker; with the daemon pool
it returns almost immediately.
"""

import pathlib
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
STUCK_SECONDS = 12.0
EXIT_BUDGET_SECONDS = 5.0
MARKER = "MAIN_RETURNED"


def _run_child(script: str) -> tuple[float, str]:
    """Run *script* in a fresh interpreter; return (exit delay, child stdout).

    The exit delay is the time between the child printing its MAIN_RETURNED
    marker (its main thread is done) and the process ending.
    """
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    finished = time.time()
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr[-3000:]}"
    markers = [line.split()[1] for line in proc.stdout.splitlines() if line.startswith(MARKER)]
    assert markers, f"child never reached its main-thread return:\n{proc.stdout}\n{proc.stderr[-3000:]}"
    return finished - float(markers[-1]), proc.stdout


_ASYNC_BRIDGE_SCRIPT = f"""
import asyncio, sys, threading, time
sys.path.insert(0, {str(REPO_ROOT)!r})
from model_tools import _run_async

async def stuck_tool():
    await asyncio.sleep({STUCK_SECONDS})

def abandoned_caller():
    async def driver():
        # Called from inside a running loop, so _run_async runs the coroutine on
        # its own worker thread; this caller is abandoned when main returns.
        _run_async(stuck_tool())
    asyncio.run(driver())

threading.Thread(target=abandoned_caller, daemon=True).start()
time.sleep(0.5)
print("{MARKER}", time.time(), flush=True)
"""


def test_async_tool_bridge_worker_does_not_hold_exit():
    delay, _ = _run_child(_ASYNC_BRIDGE_SCRIPT)
    assert delay < EXIT_BUDGET_SECONDS, (
        f"process took {delay:.1f}s to exit after its main thread returned; "
        f"a stuck _run_async worker is holding interpreter exit"
    )


_ZAI_PROBE_SCRIPT = f"""
import sys, threading, time
sys.path.insert(0, {str(REPO_ROOT)!r})
import hermes_cli.auth_zai_kimi as zai

def stuck_probe(api_key, endpoint, timeout):
    time.sleep({STUCK_SECONDS})
    return None

zai._probe_single_zai_endpoint = stuck_probe
# detect_zai_endpoint waits for every probe; its caller is abandoned here.
threading.Thread(
    target=lambda: zai.detect_zai_endpoint("test-key", timeout=1.0), daemon=True
).start()
time.sleep(0.5)
print("{MARKER}", time.time(), flush=True)
"""


def test_zai_endpoint_probes_do_not_hold_exit():
    delay, _ = _run_child(_ZAI_PROBE_SCRIPT)
    assert delay < EXIT_BUDGET_SECONDS, (
        f"process took {delay:.1f}s to exit after its main thread returned; "
        f"an in-flight Z.AI endpoint probe is holding interpreter exit"
    )


_SECRET_FETCH_SCRIPT = f"""
import sys, time
from pathlib import Path
sys.path.insert(0, {str(REPO_ROOT)!r})
from agent.secret_sources.base import FetchResult
from agent.secret_sources.registry import _fetch_with_timeout

class SlowSource:
    name = "slow-test-source"

    def fetch_timeout_seconds(self, cfg):
        return 0.5

    def fetch(self, cfg, home_path):
        time.sleep({STUCK_SECONDS})
        return FetchResult()

result = _fetch_with_timeout(SlowSource(), {{}}, Path({str(REPO_ROOT)!r}), {{}})
print("RESULT_OK", result.ok, flush=True)
print("{MARKER}", time.time(), flush=True)
"""


def test_secret_source_fetch_worker_does_not_hold_exit():
    delay, stdout = _run_child(_SECRET_FETCH_SCRIPT)
    # The budget elapsed, so the call itself must have reported a timeout.
    assert "RESULT_OK False" in stdout, stdout
    assert delay < EXIT_BUDGET_SECONDS, (
        f"process took {delay:.1f}s to exit after its main thread returned; "
        f"an abandoned secret-source fetch is holding interpreter exit"
    )
