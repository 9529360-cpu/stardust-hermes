"""A terminal command is ended together with everything it started, on native Windows.

Git Bash runs a command through fork+exec emulation, so the program that finally runs (``sleep``, ``git``, ``curl``,
``tail``...) can outlive every ancestor in the Windows process tree: its recorded parent has already exited.
``taskkill /T`` and psutil both walk parent links, so a timeout or /stop that "killed the tree" left the command
running, holding the output pipe open, and the partial output it had printed was lost behind the wait backstop.

These tests start real commands. Each uses a ``sleep`` duration no other process has, so a leftover can be told
from anyone else's ``sleep`` and only that one is ever cleaned up.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
import uuid

import psutil
import pytest

from tools.environments.local import LocalEnvironment


def _unique_seconds() -> str:
    return f"{7000 + uuid.uuid4().int % 1000}.{uuid.uuid4().int % 10**6:06d}"


def _sleepers(seconds: str) -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        with contextlib.suppress(psutil.Error):
            if (proc.info["name"] or "").lower() == "sleep.exe" and seconds in (proc.info["cmdline"] or []):
                found.append(proc)
    return found


def _wait_until(condition, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


@pytest.fixture
def seconds():
    value = _unique_seconds()
    yield value
    for proc in _sleepers(value):  # only this test's own sleep: name and unique argument both match
        with contextlib.suppress(psutil.Error):
            proc.kill()


@pytest.mark.windows_only
def test_timeout_ends_the_command_and_keeps_what_it_printed(seconds):
    # The deadline is generous so a slow Git Bash start cannot let it pass before the command has printed.
    result = LocalEnvironment().execute(f"echo printed-before-timeout && sleep {seconds}", timeout=5)

    assert result["returncode"] == 124
    assert "printed-before-timeout" in result["output"]
    assert "timed out" in result["output"].lower()
    assert _wait_until(lambda: not _sleepers(seconds), 5), "the timed-out command is still running"


@pytest.mark.windows_only
def test_interrupt_ends_the_command(seconds):
    from tools.interrupt import set_interrupt

    env, box = LocalEnvironment(), {}
    worker = threading.Thread(target=lambda: box.update(result=env.execute(f"sleep {seconds}", timeout=60)), daemon=True)
    worker.start()
    assert _wait_until(lambda: _sleepers(seconds), 20), "the command never started"
    set_interrupt(True, thread_id=worker.ident)
    try:
        worker.join(30)
        assert not worker.is_alive(), "the interrupted command did not return"
    finally:
        set_interrupt(False, thread_id=worker.ident)

    assert box["result"]["returncode"] == 130
    assert _wait_until(lambda: not _sleepers(seconds), 5), "the interrupted command is still running"


@pytest.mark.windows_only
def test_command_that_finishes_normally_leaves_its_background_job_running(seconds):
    """Containment must not turn ``cmd &`` into a command that dies with its shell. The job's output goes away so it
    does not hold the capture pipe (a job that does is a separate matter)."""
    result = LocalEnvironment().execute(f"sleep {seconds} >/dev/null 2>&1 & echo started", timeout=30)

    assert result["returncode"] == 0
    assert "started" in result["output"]
    assert _sleepers(seconds), "finishing a command must not kill what it started in the background"


@pytest.mark.windows_only
def test_killing_a_background_process_ends_its_whole_tree(seconds, tmp_path):
    from tools.process_registry import ProcessRegistry

    registry = ProcessRegistry()
    session = registry.spawn_local(f"echo started && sleep {seconds}", cwd=str(tmp_path), task_id="job-containment")
    assert _wait_until(lambda: _sleepers(seconds), 20), "the background command never started"

    registry.kill_process(session.id, source="test")

    assert _wait_until(lambda: not _sleepers(seconds), 5), "the killed background process is still running"


@pytest.mark.windows_only
def test_a_command_handed_to_the_background_can_still_be_killed_whole(seconds, tmp_path):
    """Yield-to-background adopts the live Popen; the adopted session must keep the means to end its tree."""
    from tools.process_registry import ProcessRegistry

    env = LocalEnvironment()
    proc = env._run_bash(env._wrap_command(f"sleep {seconds}", env.cwd), timeout=60)
    registry = ProcessRegistry()
    session = registry.adopt_local(proc, command=f"sleep {seconds}", cwd=str(tmp_path), task_id="handoff")
    assert _wait_until(lambda: _sleepers(seconds), 20), "the command never started"

    registry.kill_process(session.id, source="test")

    assert _wait_until(lambda: not _sleepers(seconds), 5), "the adopted process is still running"


@pytest.mark.windows_only
def test_command_still_runs_when_no_job_can_hold_it(monkeypatch):
    """If the host already sits in a job that refuses nesting, the command runs uncontained instead of failing."""
    from tools.environments import windows_job

    def refuse(self, proc):
        raise OSError("access denied")

    monkeypatch.setattr(windows_job.ProcessJob, "assign", refuse)
    proc = windows_job.spawn_contained(
        [sys.executable, "-c", "print('ran')"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=30)

    assert out.strip() == "ran"
    assert windows_job.terminate_contained(proc) is False


@pytest.mark.windows_only
def test_a_released_job_can_no_longer_be_terminated():
    from tools.environments import windows_job

    proc = windows_job.spawn_contained([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert isinstance(proc._hermes_job, windows_job.ProcessJob)
        proc._hermes_job.close()

        assert windows_job.terminate_contained(proc) is False
    finally:
        proc.kill()
        proc.wait(timeout=30)


@pytest.mark.windows_only
def test_a_process_outside_any_job_is_still_ended_by_the_fallback():
    """Sessions recovered after a restart, or a host that refuses nesting, have no job; the old kill must still work."""
    from tools.environments.local import _kill_process_windows

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _kill_process_windows(proc)

        assert proc.wait(timeout=30) is not None
    finally:
        with contextlib.suppress(OSError):
            proc.kill()


def test_a_popen_double_is_returned_untouched(monkeypatch):
    """Tests swap a double in for ``subprocess.Popen``. It made no real process, so containment must not run Win32
    calls on it (job assignment, resume) or treat it as contained."""
    from unittest.mock import MagicMock

    from tools.environments import windows_job

    double = MagicMock()
    double.pid = 4321
    monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=double))

    assert windows_job.spawn_contained(["bash", "-c", "true"], text=True) is double
    assert windows_job.terminate_contained(double) is False


def test_only_a_real_job_counts_as_containment():
    """A stand-in process (mocks, namespaces) must never be mistaken for a contained one, or its caller would skip
    the kill it relies on."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from tools.environments import windows_job

    assert windows_job.terminate_contained(MagicMock()) is False
    assert windows_job.terminate_contained(SimpleNamespace(_hermes_job=object())) is False
    assert windows_job.terminate_contained(SimpleNamespace()) is False


@pytest.mark.skipif(sys.platform == "win32", reason="off Windows the helper must be a plain Popen")
def test_spawn_is_a_plain_popen_off_windows():
    from tools.environments import windows_job

    proc = windows_job.spawn_contained([sys.executable, "-c", "print('ran')"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=30)

    assert out.strip() == "ran"
    assert not hasattr(proc, "_hermes_job")
    assert windows_job.terminate_contained(proc) is False
