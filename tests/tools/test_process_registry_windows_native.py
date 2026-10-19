"""The process registry on native Windows: real Git Bash, real job objects, real ConPTY.

``test_process_registry.py`` fakes the operating system, and its POSIX tests (orphaned pipes, process groups, ``select``,
``killpg``) are skipped on Windows, so nothing else exercises what a Windows user's background commands actually go
through. Every test here starts real processes.

A process a test starts carries a token in its command line, and only processes carrying that token are ever cleaned up.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shlex
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import psutil
import pytest

from tools.process_registry import ProcessRegistry

pytestmark = pytest.mark.windows_only

_CREATE_NO_WINDOW = 0x08000000


def _wait_until(condition, timeout: float = 20.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


def _bounded(call, seconds: float):
    """``(finished, value)`` of ``call`` run on a daemon thread, so a deadlock fails the test instead of hanging the run."""
    box: dict = {}

    def run():
        try:
            box["value"] = call()
        except BaseException as exc:  # a PanicException is not an Exception
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if "error" in box:
        raise box["error"]
    return (not worker.is_alive()), box.get("value")


def _carrying(token: str) -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["cmdline"]):
        with contextlib.suppress(psutil.Error):
            if token in " ".join(proc.info["cmdline"] or []):
                found.append(proc)
    return found


@pytest.fixture
def token():
    value = f"sdtok{uuid.uuid4().hex[:16]}"
    yield value
    for proc in _carrying(value):  # only this test's own processes: the unique token is in their command line
        with contextlib.suppress(psutil.Error):
            proc.kill()


@pytest.fixture
def registry(tmp_path, monkeypatch, token):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "hermes-home"))
    reg = ProcessRegistry()
    yield reg
    for sid in list(reg._running):
        with contextlib.suppress(Exception):
            reg.kill_process(sid)


def _script(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _command(script: Path, *args: str) -> str:
    """The Git Bash command line that runs ``script`` with this test's interpreter."""
    return " ".join(shlex.quote(part) for part in (Path(sys.executable).as_posix(), script.as_posix(), *args))


def _exited(registry: ProcessRegistry, session_id: str, timeout: float = 30.0) -> dict:
    result: dict = {}

    def settled() -> bool:
        nonlocal result
        result = registry.poll(session_id)
        return result["status"] == "exited"

    assert _wait_until(settled, timeout), f"the process did not exit: {result}"
    return result


def _output(registry: ProcessRegistry, session_id: str) -> str:
    return registry.poll(session_id)["output_preview"]


# A launcher that leaves a descendant behind holding the capture pipe, with the launcher already gone: the process
# whose recorded parent has exited, which is what a command run through Git Bash's fork+exec emulation leaves.
_ORPHAN_SPAWNER = """
    import subprocess, sys
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)  # " + sys.argv[1] + "-holder"],
        stdout=sys.stdout, stderr=sys.stderr, stdin=subprocess.DEVNULL)
    print("spawned-holder", flush=True)
"""


def _spawn_with_orphan(registry: ProcessRegistry, tmp_path: Path, token: str):
    script = _script(tmp_path, "spawn_orphan.py", _ORPHAN_SPAWNER)
    session = registry.spawn_local(_command(script, token), cwd=str(tmp_path))
    assert _wait_until(lambda: _carrying(f"{token}-holder"), 30), "the descendant never started"
    return session


class TestOrphanedDescendant:
    """The direct child is gone but a descendant still holds the capture pipe, so the reader never sees EOF (#17327).
    Windows pipes have no ``select()``: the lazy reconcile in poll()/wait() is the only way out."""

    def test_poll_reports_the_exit_while_the_descendant_still_holds_the_pipe(self, registry, token, tmp_path):
        session = _spawn_with_orphan(registry, tmp_path, token)

        finished, result = False, None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            finished, result = _bounded(lambda: registry.poll(session.id), 15)
            assert finished, "poll() blocked: releasing the pipe a parked reader holds waits on that reader"
            if result["status"] == "exited":
                break
            time.sleep(0.2)

        assert result["status"] == "exited"
        assert result["exit_code"] == 0
        assert "spawned-holder" in result["output_preview"]

    def test_wait_returns_when_the_command_exits_although_the_descendant_keeps_the_pipe(self, registry, token, tmp_path):
        session = _spawn_with_orphan(registry, tmp_path, token)

        finished, result = _bounded(lambda: registry.wait(session.id, timeout=30), 45)

        assert finished, "wait() never returned"
        assert result["status"] == "exited"
        assert result["exit_code"] == 0

    def test_kill_ends_the_descendant_whose_parent_is_already_gone(self, registry, token, tmp_path):
        """taskkill /T and psutil walk parent links, so only the job the command was spawned into reaches it."""
        session = _spawn_with_orphan(registry, tmp_path, token)

        result = registry.kill_process(session.id)

        assert result["status"] == "killed"
        assert _wait_until(lambda: not _carrying(token), 15), "the orphaned descendant survived the kill"


_TREE = """
    import subprocess, sys, time
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)  # " + sys.argv[1] + "-child"])
    print("tree-up", flush=True)
    time.sleep(120)
"""


class TestRecoveredSession:
    """After a gateway restart a session has no Popen and no job: its process is known only by PID and start time."""

    def test_a_recovered_session_is_killed_with_its_whole_tree(self, registry, token, tmp_path):
        script = _script(tmp_path, "tree.py", _TREE)
        session = registry.spawn_local(_command(script, token), cwd=str(tmp_path), task_id="recovery")
        assert _wait_until(lambda: len(_carrying(f"{token}-child")) >= 1, 30), "the child never started"

        restarted = ProcessRegistry()
        assert restarted.recover_from_checkpoint() == 1
        recovered = restarted.get(session.id)
        assert recovered is not None and recovered.detached is True

        result = restarted.kill_process(session.id)

        assert result["status"] == "killed"
        assert _wait_until(lambda: not _carrying(token), 15), "part of the recovered tree is still running"

    def test_a_recycled_pid_is_not_adopted(self, registry, token, tmp_path):
        script = _script(tmp_path, "sleeper.py", "import time\ntime.sleep(120)\n")
        session = registry.spawn_local(_command(script, token), cwd=str(tmp_path), task_id="recycled")
        assert _wait_until(lambda: _carrying(token), 30), "the process never started"
        checkpoint = Path(os.environ["STARDUST_HOME"]) / "processes.json"
        entries = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert [e["session_id"] for e in entries] == [session.id]
        entries[0]["host_start_time"] = int(entries[0]["host_start_time"]) + 1  # the number now names a stranger
        checkpoint.write_text(json.dumps(entries), encoding="utf-8")

        restarted = ProcessRegistry()

        assert restarted.recover_from_checkpoint() == 0
        assert restarted.get(session.id) is None
        assert _carrying(token), "refusing to adopt the PID must not touch the process now holding it"


def test_a_failed_tracker_start_leaves_no_process_behind(registry, token, tmp_path, monkeypatch):
    script = _script(tmp_path, "sleeper.py", "import time\ntime.sleep(120)\n")
    real_start = threading.Thread.start

    def refuse(self):
        if self.name.startswith("proc-reader-"):
            raise RuntimeError("can't start new thread")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", refuse)

    with pytest.raises(RuntimeError, match="can't start new thread"):
        registry.spawn_local(_command(script, token), cwd=str(tmp_path))

    assert _wait_until(lambda: not _carrying(token), 15), "the untracked process was left running"
    assert not registry._running


def test_background_commands_start_without_a_console_window(registry, tmp_path, monkeypatch):
    started: list[tuple[str, int]] = []
    guarded_popen = subprocess.Popen

    class Recording(guarded_popen):
        def __init__(self, argv, *args, **kwargs):
            started.append((str(argv[0]).lower(), kwargs.get("creationflags", 0)))
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)

    session = registry.spawn_local("echo quiet", cwd=str(tmp_path))
    _exited(registry, session.id)

    shell_flags = [flags for program, flags in started if "bash" in program]
    assert shell_flags, f"the shell was never started through subprocess.Popen: {started}"
    assert all(flags & _CREATE_NO_WINDOW for flags in shell_flags)


def test_the_shell_start_up_warnings_do_not_head_a_commands_output(registry, tmp_path):
    """Git Bash prints "no job control in this shell" when it starts without a terminal; it is not the command's output."""
    session = registry.spawn_local("echo clean-output", cwd=str(tmp_path))
    _exited(registry, session.id)

    assert "no job control" not in session.output_buffer
    assert session.output_buffer.endswith("clean-output\n")


def test_output_in_the_console_code_page_is_decoded(registry, tmp_path):
    """A native program writes in the OEM code page (GBK on a Chinese system), not in UTF-8."""
    code_page = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    try:
        raw = "café-ü\n".encode(code_page)
    except (LookupError, UnicodeEncodeError):
        pytest.skip(f"{code_page} cannot hold the sample text")
    script = _script(
        tmp_path, "oem.py", f"import sys\nsys.stdout.buffer.write({raw!r})\nsys.stdout.buffer.flush()\n")

    session = registry.spawn_local(_command(script), cwd=str(tmp_path))
    _exited(registry, session.id)

    assert "café-ü" in session.output_buffer


_READ_A_LINE = """
    import sys
    print("READY", flush=True)
    line = sys.stdin.readline()
    print("GOT", ascii(line), flush=True)
"""
_READ_UNTIL_EOF = """
    import sys
    print("READY", flush=True)
    data = sys.stdin.read()
    print("EOFGOT", ascii(data), flush=True)
"""


@pytest.fixture
def pty_session(registry, tmp_path):
    """Start ``source`` in a real ConPTY and wait until the child says it is ready for input."""
    pytest.importorskip("winpty")

    def start(source: str):
        session = registry.spawn_local(_command(_script(tmp_path, "pty_child.py", source)), cwd=str(tmp_path), use_pty=True)
        assert session._pty is not None, "no ConPTY was created"
        assert _wait_until(lambda: "READY" in _output(registry, session.id), 30), "the child never became ready"
        return session

    return start


class TestRealConPty:
    def test_submit_reaches_a_child_reading_a_line(self, registry, pty_session):
        session = pty_session(_READ_A_LINE)

        assert registry.submit_stdin(session.id, "hello")["status"] == "ok"

        _exited(registry, session.id, 30)
        assert "GOT 'hello\\n'" in _output(registry, session.id)

    def test_a_lone_surrogate_does_not_panic_the_writer(self, registry, pty_session):
        """Undecodable input bytes travel as lone surrogates. pywinpty panics on them with an exception that
        ``except Exception`` does not stop, so one such character took the whole tool call down."""
        session = pty_session(_READ_A_LINE)

        result = registry.write_stdin(session.id, "\udcff\r\n")

        assert result["status"] == "ok"
        _exited(registry, session.id, 30)
        assert "GOT '\\ufffd" in _output(registry, session.id)

    def test_close_stdin_ends_the_input_of_a_child_reading_until_eof(self, registry, pty_session):
        session = pty_session(_READ_UNTIL_EOF)
        registry.submit_stdin(session.id, "abc")

        try:
            assert registry.close_stdin(session.id)["status"] == "ok"
            _exited(registry, session.id, 10)
            assert "EOFGOT 'abc\\n'" in _output(registry, session.id)
            assert registry.close_stdin(session.id)["status"] == "already_exited"
        finally:
            registry.kill_process(session.id)
