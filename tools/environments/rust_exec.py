"""Opt-in JSONL execution adapter; the default local spawn path never imports it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from hermes_cli._subprocess_compat import windows_hide_flags
from tools.environments.windows_job import spawn_contained, terminate_contained


def enabled() -> bool:
    return os.environ.get('STARDUST_EXEC_BACKEND') == 'rust'


class RustProcess:
    """ProcessHandle exposing helper events as the existing raw UTF-8 output pipe."""

    def __new__(cls, *args, **kwargs):
        try:
            from stardust_native import NativeChild  # noqa: F401
        except ImportError:
            return super().__new__(cls)
        from tools.environments.native_process import NativeProcess
        return NativeProcess(*args, **kwargs)

    def __init__(self, argv, *, cwd, env, timeout, stdin_data):
        name = 'stardust-exec.exe' if os.name == 'nt' else 'stardust-exec'
        binary = shutil.which(name)
        if binary is None:
            candidate = Path(__file__).resolve().parents[2] / 'target' / 'release' / name
            if not candidate.is_file():
                raise FileNotFoundError('Rust backend requires cargo build --release -p stardust-exec or stardust-exec on PATH')
            binary = str(candidate)
        self._proc = spawn_contained(
            [binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=windows_hide_flags(),
            start_new_session=True, env=env, cwd=cwd)
        self._hermes_rust_exec = True
        self.pid = self._proc.pid
        self._hermes_job = getattr(self._proc, '_hermes_job', None)
        if os.name == 'nt' and self._hermes_job is None:
            self._proc.kill()
            self._proc.wait()
            self._proc.stdin.close()
            self._proc.stdout.close()
            raise OSError('Rust backend requires Windows job containment for cancellation')
        self.returncode = None
        self._child_pid = None
        self.elapsed_ms = None
        self._done = threading.Event()
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'r', encoding='utf-8', errors='replace')
        request = dict(argv=argv, cwd=cwd, env=env,
                       timeout_ms=max(1, int(timeout * 1000)), stdin=stdin_data)

        def worker():
            code = 1
            try:
                # Write concurrently: a large stdin payload can exceed the pipe buffer.
                def send():
                    try:
                        self._proc.stdin.write((json.dumps(request) + '\n').encode('utf-8'))
                        self._proc.stdin.close()
                    except (OSError, ValueError):
                        pass
                threading.Thread(target=send, daemon=True).start()
                with os.fdopen(write_fd, 'wb', buffering=0) as output:
                    for line in self._proc.stdout:
                        event = json.loads(line)
                        if 'started_pid' in event:
                            self._child_pid = event['started_pid']
                        if 'output' in event:
                            output.write(event['output'].encode('utf-8'))
                        if 'error' in event:
                            output.write(('Rust exec error: ' + event['error']).encode('utf-8'))
                        if 'exit_code' in event:
                            code = event['exit_code']
                            self.elapsed_ms = event['elapsed_ms']
                            if event.get('timed_out'):
                                output.write(f'\n[Command timed out after {timeout}s]'.encode('utf-8'))
            finally:
                self._proc.stdout.close()
                self._proc.wait()
                self.returncode = code
                self._done.set()
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired('stardust-exec', timeout)
        return self.returncode

    def kill(self):
        if os.name == 'nt':
            import psutil
            descendants = psutil.Process(self.pid).children(recursive=True)
            if not terminate_contained(self._proc):
                self._proc.kill()
            _, alive = psutil.wait_procs(descendants, timeout=2)
            if alive:
                raise TimeoutError('Rust command descendants did not exit after job termination')
        else:
            import signal
            if self._child_pid is not None:
                try:
                    os.killpg(self._child_pid, signal.SIGKILL)  # windows-footgun: ok — POSIX branch only
                except ProcessLookupError:
                    pass
            try:
                os.killpg(self.pid, signal.SIGKILL)  # windows-footgun: ok — POSIX branch only
            except ProcessLookupError:
                pass
