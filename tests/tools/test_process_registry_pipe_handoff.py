"""Pipe handoff between a local process's reader thread and a reconciled exit.

Shell start-up noise is removed from the head of the output however the kernel splits it, and a
reconciled exit drains the pipe through that same path. The filter cases are pure and run everywhere;
the pipe cases use real POSIX pipes and gate files instead of timing sleeps.
"""

import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time

import pytest

from tools.process_registry import ProcessRegistry, ProcessSession
from tools.process_registry_shell_noise import filter_shell_noise_head

NOISE_CPGROUP = "bash: cannot set terminal process group (-1): Inappropriate ioctl for device"
NOISE_JOBCONTROL = "bash: no job control in this shell"
STREAM = f"{NOISE_CPGROUP}\n{NOISE_JOBCONTROL}\nowner-output\n"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes (sh, select, fcntl)")


def _filter_stream(chunks):
    head_open, pending, emitted = True, "", []
    for chunk in chunks:
        emit, pending, head_open = filter_shell_noise_head(head_open, pending, chunk)
        emitted.append(emit)
    emit, _, _ = filter_shell_noise_head(head_open, pending, "", final=True)
    emitted.append(emit)
    return "".join(emitted)


@pytest.mark.parametrize("split", range(1, len(STREAM)))
def test_warnings_split_at_any_byte_filter_like_the_whole_stream(split):
    assert _filter_stream([STREAM[:split], STREAM[split:]]) == "owner-output\n"


def test_whole_warnings_are_dropped_before_real_output():
    assert _filter_stream([STREAM]) == "owner-output\n"


def test_text_that_is_not_shell_noise_is_kept():
    assert _filter_stream(["bash: foo: command not found\n"]) == "bash: foo: command not found\n"
    assert _filter_stream(["Password: "]) == "Password: "


def test_warning_text_after_real_output_is_kept():
    assert _filter_stream(["ready\n", NOISE_JOBCONTROL + "\n"]) == "ready\n" + NOISE_JOBCONTROL + "\n"


def test_unterminated_warning_is_decided_at_end_of_stream():
    assert _filter_stream([NOISE_JOBCONTROL]) == ""


def test_reader_and_reconciled_drain_share_one_decoder():
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_utf8_handoff", command="probe")
    # The reader consumed the first byte of "é"; the reconciled drain gets the rest.
    registry._ingest_pipe_bytes(session, b"caf\xc3")
    registry._ingest_pipe_bytes(session, b"\xa9\n", final=True)
    assert session.output_buffer == "café\n"


def _spawn(script: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        start_new_session=True,
    )


def _local_session(proc: subprocess.Popen, sid: str) -> ProcessSession:
    return ProcessSession(
        id=sid, command="probe", task_id="t", owner_task_id="o", session_key="k",
        process=proc, pid=proc.pid, notify_on_complete=True,
    )


def _pipe_has_data(stream) -> bool:
    return bool(select.select([stream.fileno()], [], [], 0)[0])


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _completion_for(registry: ProcessRegistry, session_id: str) -> dict:
    while not registry.completion_queue.empty():
        event = registry.completion_queue.get_nowait()
        if event.get("type") == "completion" and event["session_id"] == session_id:
            return event
    raise AssertionError(f"no completion event for {session_id}")


def _reap(proc: subprocess.Popen, *readers) -> None:
    try:
        # Kill the whole session so background descendants die with it. POSIX-only, like the tests using it.
        os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # windows-footgun: ok - POSIX-only helper
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)
    for reader in readers:
        reader.join(timeout=10)


@posix_only
def test_warning_split_across_reads_stays_out_of_output(tmp_path):
    first, gate = tmp_path / "first", tmp_path / "gate"
    script = (
        f"printf '%s\\n' {shlex.quote(NOISE_CPGROUP)}; touch {shlex.quote(str(first))}; "
        f"while [ ! -e {shlex.quote(str(gate))} ]; do sleep 0.01; done; "
        f"printf '%s\\n' {shlex.quote(NOISE_JOBCONTROL)} owner-output"
    )
    registry = ProcessRegistry()
    live = []
    registry.on_output = lambda _session, chunk: live.append(chunk)
    proc = _spawn(script)
    session = _local_session(proc, "proc_split_reads")
    registry._track_started(session, registry._reader_loop, "split-reader")
    try:
        # The reader takes the first warning as its own read; only then is the second one written.
        assert _wait_until(lambda: first.exists() and not _pipe_has_data(proc.stdout))
        gate.touch()
        proc.wait(timeout=10)
        session._reader_thread.join(timeout=10)
        assert session.output_buffer == "owner-output\n"
        assert live == ["owner-output\n"]
    finally:
        _reap(proc, session._reader_thread)


@posix_only
def test_reconciled_exit_drains_pipe_through_the_same_filter():
    registry = ProcessRegistry()
    live = []
    registry.on_output = lambda _session, chunk: live.append(chunk)
    proc = _spawn(f"printf '%s\\n' {shlex.quote(NOISE_CPGROUP)} {shlex.quote(NOISE_JOBCONTROL)} owner-output")
    try:
        proc.wait(timeout=10)  # The direct child is gone; its output waits in the pipe, unread.
        session = _local_session(proc, "proc_drain")
        registry._running[session.id] = session  # No reader thread: the reconciled exit is the only reader.
        registry._reconcile_local_exit(session)
        event = _completion_for(registry, session.id)
        assert event["exit_code"] == 0
        assert event["output"] == "owner-output\n"
        assert live == ["owner-output\n"]
    finally:
        _reap(proc)


@posix_only
def test_stuck_output_sink_cannot_block_a_listing():
    registry = ProcessRegistry()
    entered, release = threading.Event(), threading.Event()

    def stuck_sink(_session, _chunk):
        entered.set()
        release.wait(30)

    registry.on_output = stuck_sink
    # The direct child prints and exits; a background descendant keeps the pipe open.
    proc = _spawn("printf '%s\\n' owner-output; (sleep 30) &")
    session = _local_session(proc, "proc_sink")
    registry._track_started(session, registry._reader_loop, "sink-reader")
    try:
        assert entered.wait(10), "reader never reached the output sink"
        proc.wait(timeout=10)
        worker = threading.Thread(target=registry.list_sessions, kwargs={"session_key": "k"}, daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "listing waited behind a reader stuck in its output sink"
        release.set()
        # Wait for the completion itself: `exited` is set a moment before it is published.
        event = registry.completion_queue.get(timeout=10)
        assert event["session_id"] == session.id
        assert event["output"] == "owner-output\n"
    finally:
        release.set()
        _reap(proc, session._reader_thread)
