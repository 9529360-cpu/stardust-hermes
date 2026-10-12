"""Pipe handoff between a local process's reader thread and a reconciled exit.

Shell start-up noise is removed from the head of the output however the kernel splits it, and a
reconciled exit drains the pipe through that same path. A finish that skipped or failed the drain, or
that was followed by more output, is flagged: the completion event and the receipt say the output may
be incomplete. The filter and message cases are pure. The pipe cases use real POSIX pipes and are
skipped on Windows; ordering uses gate files rather than sleeps.
"""

import json
import logging
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from hermes_constants import get_hermes_home
from tools import process_registry as process_registry_module
from tools.process_registry import (
    _PIPE_HANDOFF_BUDGET_SECONDS,
    _PIPE_HANDOFF_TIMEOUT_SECONDS,
    FINISHED_TTL_SECONDS,
    ProcessRegistry,
    ProcessSession,
)
from tools.process_registry_notifications import format_process_notification
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


def test_completion_message_says_output_may_be_incomplete():
    event = {"type": "completion", "session_id": "proc_msg", "command": "probe", "exit_code": 0,
             "completion_reason": "exited", "output": "tail\n"}
    assert "Output may be incomplete" in format_process_notification({**event, "output_incomplete": True})
    assert "Output may be incomplete" not in format_process_notification({**event, "output_incomplete": False})


def test_late_watch_match_after_exit_is_logged(caplog):
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_watch_late", command="probe", watch_patterns=["ready"],
                             exited=True, exit_code=0)
    caplog.set_level(logging.INFO, logger="tools.process_registry")
    registry._check_watch_patterns(session, "ready now\nready again\n")
    drops = [record for record in caplog.records if "Dropped watch match" in record.getMessage()]
    assert len(drops) == 2  # one log line per dropped match
    assert all(record.levelno == logging.INFO for record in drops)
    assert registry.completion_queue.empty()


def test_finish_reader_flush_failure_is_logged_as_warning(caplog):
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_flush_fail", command="probe")
    decoder = MagicMock()
    decoder.decode.side_effect = ValueError("truncated sequence")
    appended = []
    caplog.set_level(logging.WARNING, logger="tools.process_registry")
    registry._finish_reader(session, decoder, appended.append, "Process", MagicMock(), lambda: 0)
    assert any(record.levelno == logging.WARNING and "could not be flushed" in record.getMessage()
               for record in caplog.records)
    assert appended == []
    assert session.exited is True  # the finish still happens


def test_output_after_finish_marks_the_receipt_incomplete(monkeypatch):
    monkeypatch.setenv("STARDUST_SESSION_ID", "owner-session")
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_late_output", command="probe", parent_session_id="owner-session",
                             exited=True, exit_code=0, output_buffer="done\n")
    registry._running[session.id] = session
    registry._move_to_finished(session)
    assert _receipt(session.id)["output_incomplete"] is False
    registry._ingest_output(session, "late line\n")  # arrives after the finish
    assert session.output_incomplete is True
    record = _receipt(session.id)
    assert record["output_incomplete"] is True
    assert "late line" in record["output"]
    registry._ingest_output(session, "later line\n")  # more output, after the flag was already written
    registry._move_to_finished(session)  # the reader finishing refreshes the receipt with it
    assert "later line" in _receipt(session.id)["output"]
    registry._finished.pop(session.id)  # pruned from memory: get() now serves the receipt
    fresh = ProcessRegistry()
    recovered = fresh.get(session.id)
    assert recovered is not None and recovered.output_incomplete is True
    polled = fresh.poll(session.id)
    assert polled["output_incomplete"] is True
    assert "may be incomplete" in polled["warning"]


def test_failed_drain_marks_the_completion_incomplete():
    class _UnreadablePipe:
        def fileno(self):
            raise OSError("simulated: the pipe cannot be read")

        def close(self):
            pass

    class _ExitedChild:
        pid = 0
        returncode = 0
        stderr = None
        stdin = None
        stdout = _UnreadablePipe()

        def poll(self):
            return 0

    registry = ProcessRegistry()
    session = _local_session(_ExitedChild(), "proc_drain_failed")
    registry._running[session.id] = session
    registry._reconcile_local_exit(session)
    assert _completion_for(registry, session.id)["output_incomplete"] is True


def _spawn(script: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        start_new_session=True,
    )


def _local_session(proc, sid: str) -> ProcessSession:
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


def _receipt(session_id: str) -> dict:
    return json.loads((get_hermes_home() / "logs" / "process-results" / f"{session_id}.json").read_text(
        encoding="utf-8"))


def _reap(proc, *readers) -> None:
    try:
        # Kill the whole session so background descendants die with it. POSIX-only, like the tests using it.
        os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # windows-footgun: ok - POSIX-only helper
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)
    for reader in readers:
        reader.join(timeout=10)


def _timed_listing(registry: ProcessRegistry, timeout: float = 10.0):
    """One list_sessions call in a daemon thread, so a blocked listing fails the test instead of hanging it.
    Returns (seconds, rows)."""
    done = {}

    def run():
        start = time.monotonic()
        done["rows"] = registry.list_sessions(session_key="k")
        done["seconds"] = time.monotonic() - start

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    assert not worker.is_alive(), "listing did not return"
    return done["seconds"], done["rows"]


def _start_stuck_sink(registry: ProcessRegistry, sid: str):
    """A reader whose live-output sink stays stuck after the first chunk. The direct child prints and
    exits; a background descendant keeps the pipe open. Returns (session, proc, release)."""
    entered, release = threading.Event(), threading.Event()

    def stuck_sink(_session, _chunk):
        entered.set()
        release.wait(30)

    registry.on_output = stuck_sink
    proc = _spawn("printf '%s\\n' owner-output; (sleep 30) &")
    session = _local_session(proc, sid)
    registry._track_started(session, registry._reader_loop, f"{sid}-reader")
    assert entered.wait(10), "reader never reached the output sink"
    proc.wait(timeout=10)
    return session, proc, release


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
        assert event["output_incomplete"] is False  # the drain completed
        assert live == ["owner-output\n"]
    finally:
        _reap(proc)


@posix_only
def test_normal_drain_still_drops_warnings_split_from_the_reader(tmp_path):
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
    session = _local_session(proc, "proc_normal_drain")
    registry._running[session.id] = session  # No reader thread: this test plays the reader's first read.
    try:
        assert _wait_until(lambda: first.exists() and _pipe_has_data(proc.stdout))
        # The reader's first read: only the first warning is in the pipe so far.
        registry._ingest_pipe_bytes(session, os.read(proc.stdout.fileno(), 4096))
        gate.touch()
        proc.wait(timeout=10)
        registry._reconcile_local_exit(session)  # Normal drain: the lock is free.
        assert _completion_for(registry, session.id)["output"] == "owner-output\n"
        assert live == ["owner-output\n"]
    finally:
        _reap(proc)


def _reader_then_drain(registry: ProcessRegistry, tmp_path, sid: str, first: str, rest: str):
    """The reader takes `first` as its own read and holds whatever it cannot decide yet. The child then
    writes `rest` and exits, and a reconciled exit drains it. Returns (session, live, held_after_read, proc)."""
    gate, marker = tmp_path / "gate", tmp_path / "first"
    script = (
        f"printf '%s' {shlex.quote(first)}; touch {shlex.quote(str(marker))}; "
        f"while [ ! -e {shlex.quote(str(gate))} ]; do sleep 0.01; done; printf '%s' {shlex.quote(rest)}"
    )
    live = []
    registry.on_output = lambda _session, chunk: live.append(chunk)
    proc = _spawn(script)
    session = _local_session(proc, sid)
    registry._running[session.id] = session  # No reader thread: this test plays the reader's reads.
    assert _wait_until(lambda: marker.exists() and _pipe_has_data(proc.stdout))
    registry._ingest_pipe_bytes(session, os.read(proc.stdout.fileno(), 4096))
    held = session.output_buffer
    gate.touch()
    proc.wait(timeout=10)
    registry._reconcile_local_exit(session)
    return session, live, held, proc


@posix_only
def test_partial_line_held_by_the_reader_is_delivered_by_the_drain(tmp_path):
    registry = ProcessRegistry()
    session, live, held, proc = _reader_then_drain(registry, tmp_path, "proc_held_line", "ca", "lculator: 4\n")
    try:
        assert held == ""  # "ca" could still start a shell warning, so the reader holds it back
        assert _completion_for(registry, session.id)["output"] == "calculator: 4\n"
        assert live == ["calculator: 4\n"]
    finally:
        _reap(proc)


@posix_only
def test_held_warning_prefix_completed_by_the_drain_leaks_nothing(tmp_path):
    registry = ProcessRegistry()
    session, live, held, proc = _reader_then_drain(
        registry, tmp_path, "proc_held_noise", "bash: no j", "ob control in this shell\nowner-output\n")
    try:
        assert held == ""
        assert _completion_for(registry, session.id)["output"] == "owner-output\n"
        assert live == ["owner-output\n"]
    finally:
        _reap(proc)


@posix_only
def test_stuck_output_sink_cannot_block_a_listing():
    registry = ProcessRegistry()
    session, proc, release = _start_stuck_sink(registry, "proc_sink")
    try:
        seconds, _ = _timed_listing(registry)
        assert seconds < _PIPE_HANDOFF_TIMEOUT_SECONDS + 3, f"listing took {seconds:.2f}s behind the stuck sink"
        release.set()
        # Wait for the completion itself: `exited` is set a moment before it is published.
        event = registry.completion_queue.get(timeout=10)
        assert event["session_id"] == session.id
        assert event["output"] == "owner-output\n"
    finally:
        release.set()
        _reap(proc, session._reader_thread)


@posix_only
def test_stuck_output_sink_does_not_slow_repeated_listings():
    registry = ProcessRegistry()
    session, proc, release = _start_stuck_sink(registry, "proc_repeat")
    try:
        # The first busy attempt may pay the bound once. Every later listing inside the budget must return
        # at once.
        first, _ = _timed_listing(registry)
        later = [_timed_listing(registry)[0] for _ in range(5)]
        assert first < _PIPE_HANDOFF_TIMEOUT_SECONDS + 3, f"first listing took {first:.2f}s"
        assert max(later) < 0.5, f"repeated listings were slowed by the stuck sink: {later}"
    finally:
        release.set()
        _reap(proc, session._reader_thread)


@posix_only
def test_quick_repeated_listings_do_not_exhaust_the_handoff_budget():
    registry = ProcessRegistry()
    session, proc, release = _start_stuck_sink(registry, "proc_budget")
    try:
        started = time.monotonic()
        _timed_listing(registry)  # the first busy attempt pays the bound once
        for _ in range(40):  # far more quick calls than any attempt count would allow
            assert _timed_listing(registry)[1][0]["status"] == "running"
        assert time.monotonic() - started < _PIPE_HANDOFF_BUDGET_SECONDS  # the burst fits inside the budget

        def finished():
            return _timed_listing(registry)[1][0]["status"] == "exited"

        assert _wait_until(finished, timeout=_PIPE_HANDOFF_BUDGET_SECONDS + 10)
        assert time.monotonic() - started >= _PIPE_HANDOFF_BUDGET_SECONDS  # the budget was spent, not skipped
    finally:
        release.set()
        _reap(proc, session._reader_thread)


@posix_only
def test_stuck_output_sink_does_not_keep_session_running_forever():
    registry = ProcessRegistry()
    session, proc, release = _start_stuck_sink(registry, "proc_forever")
    try:
        started = time.monotonic()

        def finished():
            return _timed_listing(registry)[1][0]["status"] == "exited"

        assert _wait_until(finished, timeout=_PIPE_HANDOFF_BUDGET_SECONDS + 10), (
            "session stayed running while its sink stayed stuck")
        assert time.monotonic() - started >= _PIPE_HANDOFF_BUDGET_SECONDS
        # Finished without the unread output: the event and the receipt both say so.
        event = registry.completion_queue.get(timeout=5)
        assert event["session_id"] == session.id
        assert event["output"] == "owner-output\n"
        assert event["output_incomplete"] is True
        assert _receipt(session.id)["output_incomplete"] is True
        release.set()
        session._reader_thread.join(timeout=10)
        assert not session._reader_thread.is_alive(), "reader did not finish once its sink was released"
        assert registry.completion_queue.empty(), "the released reader published a second completion"
    finally:
        release.set()
        _reap(proc, session._reader_thread)


@posix_only
def test_reconcile_without_a_drain_marks_the_completion_and_receipt(monkeypatch):
    # Windows reconciles without draining; the same skip is exercised here on POSIX.
    monkeypatch.setattr(process_registry_module, "_IS_WINDOWS", True)
    registry = ProcessRegistry()
    proc = _spawn("printf '%s\\n' owner-output")
    proc.wait(timeout=10)
    session = _local_session(proc, "proc_no_drain")
    registry._running[session.id] = session
    try:
        registry._reconcile_local_exit(session)
        assert _completion_for(registry, session.id)["output_incomplete"] is True
        assert _receipt(session.id)["output_incomplete"] is True
    finally:
        _reap(proc)


@posix_only
def test_drain_reports_end_of_file_only_when_the_writer_is_gone(tmp_path):
    gate = tmp_path / "gate"
    proc = _spawn(f"( while [ ! -e {shlex.quote(str(gate))} ]; do sleep 0.01; done; printf 'late\\n' ) & exit 0")
    proc.wait(timeout=10)  # The direct child is gone; the background writer still holds the pipe.
    registry = ProcessRegistry()
    session = _local_session(proc, "proc_drain_eof")
    try:
        with session._pipe_read_lock:
            # No bytes right now, and the writer is alive: not end of file.
            assert registry._drain_pipe_available(session, proc.stdout) is False
            assert session.output_buffer == ""
            gate.touch()
            # Returns True only once the writer has written and gone, having read its last bytes.
            assert _wait_until(lambda: registry._drain_pipe_available(session, proc.stdout))
        assert session.output_buffer == "late\n"
    finally:
        _reap(proc)


@posix_only
def test_live_writer_after_the_drain_is_captured_and_flagged(tmp_path):
    gate = tmp_path / "gate"
    # Ticks every 50 ms until the gate opens, then a tail, then exit. The ticks keep the reader from idling out.
    writer = (
        f"( i=0; while [ ! -e {shlex.quote(str(gate))} ]; do printf 'tick%s\\n' $i; i=$((i+1)); sleep 0.05; done; "
        "j=0; while [ $j -lt 20 ]; do printf 'tail%s\\n' $j; j=$((j+1)); sleep 0.02; done ) & exit 0"
    )
    registry = ProcessRegistry()
    live = []
    registry.on_output = lambda _session, chunk: live.append(chunk)
    proc = _spawn(writer)
    session = _local_session(proc, "proc_live_writer")
    registry._track_started(session, registry._reader_loop, "live-writer-reader")
    try:
        proc.wait(timeout=10)  # The direct child exited; the writer is still writing.
        registry._reconcile_local_exit(session)  # Not end of file, so the session is flagged and the pipe stays open.
        assert session.exited and session.output_incomplete
        assert _completion_for(registry, session.id)["output_incomplete"] is True
        gate.touch()
        session._reader_thread.join(timeout=15)  # The writer exits; the reader reaches end of file and finishes.
        assert not session._reader_thread.is_alive()
        lines = session.output_buffer.splitlines()
        ticks = [line for line in lines if line.startswith("tick")]
        # Nothing dropped: the ticks run from 0 without a gap, and every tail line arrived.
        assert [int(line[4:]) for line in ticks] == list(range(len(ticks)))
        assert [line for line in lines if line.startswith("tail")] == [f"tail{j}" for j in range(20)]
        assert "".join(live) == session.output_buffer
        record = _receipt(session.id)
        assert record["output_incomplete"] is True
        assert record["output"].endswith("tail19\n")  # the finish after end of file refreshed the receipt
    finally:
        _reap(proc, session._reader_thread)


@posix_only
def test_pruning_a_finished_session_keeps_the_pipe_its_reader_is_still_reading():
    registry = ProcessRegistry()
    session, proc, release = _start_stuck_sink(registry, "proc_prune_live")
    try:
        registry._move_to_finished(session, keep_stdout=True)  # finished while its reader is still busy
        session.started_at -= FINISHED_TTL_SECONDS + 60  # long past the retention window
        with registry._lock:
            registry._prune_if_needed()
        assert session.id not in registry._finished  # pruned from memory
        assert not proc.stdout.closed  # but the pipe stays open for the reader
        release.set()
        session._reader_thread.join(timeout=15)
        assert proc.stdout.closed  # the reader closed it when it finished
    finally:
        release.set()
        _reap(proc, session._reader_thread)


def test_reader_exception_is_logged_and_marks_output_incomplete(caplog):
    class _Buffer:
        def __init__(self):
            self.reads = 0

        def read1(self, _n):
            self.reads += 1
            if self.reads == 1:
                return b"partial\n"
            raise RuntimeError("stream broke")

    class _Stdout:
        def __init__(self):
            self.buffer = _Buffer()

        def close(self):
            pass

    class _Process:
        returncode = 0
        stderr = None
        stdin = None

        def __init__(self):
            self.stdout = _Stdout()

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    registry = ProcessRegistry()
    session = ProcessSession(id="proc_reader_error", command="probe")
    session.process = _Process()
    caplog.set_level(logging.WARNING, logger="tools.process_registry")
    registry._reader_loop(session)
    assert session.output_buffer == "partial\n"
    assert session.output_incomplete is True
    assert any(record.levelno == logging.WARNING and "stopped early" in record.getMessage()
               for record in caplog.records)


def test_final_pipe_flush_failure_is_logged_and_marks_output_incomplete(caplog, monkeypatch):
    class _Buffer:
        def read1(self, _n):
            return b""  # end of file at once

    class _Stdout:
        def __init__(self):
            self.buffer = _Buffer()

        def close(self):
            pass

    class _Process:
        returncode = 0
        stderr = None
        stdin = None

        def __init__(self):
            self.stdout = _Stdout()

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    registry = ProcessRegistry()
    session = ProcessSession(id="proc_flush_error", command="probe")
    session.process = _Process()
    real_ingest = registry._ingest_pipe_bytes

    def failing_final_flush(sess, raw, *, final=False):
        if final:
            raise RuntimeError("decoder broke")
        return real_ingest(sess, raw, final=final)

    monkeypatch.setattr(registry, "_ingest_pipe_bytes", failing_final_flush)
    caplog.set_level(logging.WARNING, logger="tools.process_registry")
    registry._reader_loop(session)
    assert session.output_incomplete is True
    assert any(record.levelno == logging.WARNING and "Final flush" in record.getMessage()
               for record in caplog.records)


class _FakeEnv:
    def __init__(self, reply: str):
        self.reply = reply

    def execute(self, command, timeout=None):
        return {"output": self.reply}


def test_environment_log_output_goes_through_the_shared_filter_and_ingest():
    delta = NOISE_JOBCONTROL + "\nready now\n"
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_env_log", command="probe", watch_patterns=["ready"])
    emitted = []
    registry.on_output = lambda _session, chunk: emitted.append(chunk)
    size = len(delta.encode("utf-8"))
    assert registry._ingest_env_delta(session, _FakeEnv(f"{size} 0\n{delta}"), "/log", 0) == size
    assert session.output_buffer == "ready now\n"  # the shell warning was filtered out
    assert emitted == ["ready now\n"]  # the live sink saw the same text
    event = registry.completion_queue.get_nowait()
    assert event["type"] == "watch_match" and event["pattern"] == "ready"


def test_environment_log_rotation_restarts_the_buffer_and_flags_it():
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_env_rotated", command="probe", output_buffer="old output\n")
    assert registry._ingest_env_delta(session, _FakeEnv("4 0\nnew\n"), "/log", 100) == 4
    assert session.output_buffer == "new\n"
    assert session.output_incomplete is True


def test_environment_unusable_reply_keeps_the_offset():
    registry = ProcessRegistry()
    session = ProcessSession(id="proc_env_garbled", command="probe")
    assert registry._ingest_env_delta(session, _FakeEnv("command not found\n"), "/log", 42) == 42
    assert session.output_buffer == ""
    assert session.output_incomplete is False
