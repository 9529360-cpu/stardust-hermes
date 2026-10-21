"""In-process counterpart of the optional JSONL helper adapter."""
import os
import subprocess
import threading
import time

from tools.environments.base_output import console_output_decoder


class NativeProcess:
    _hermes_rust_exec = True

    def __init__(self, argv, *, cwd, env, timeout, stdin_data):
        from stardust_native import NativeChild

        self._child = NativeChild(argv, cwd, env, stdin_data)
        self.pid = self._child.pid
        self.returncode = None
        self.elapsed_ms = None
        self._done = threading.Event()
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'r', encoding='utf-8', errors='replace')
        start = time.monotonic()
        output = os.fdopen(write_fd, 'wb', buffering=0)
        lock = threading.Lock()

        self._reader_stop = threading.Event()

        def drain():
            decoder = console_output_decoder()
            try:
                while not self._reader_stop.is_set():
                    chunk = self._child.read()
                    if chunk is None:
                        continue
                    if not chunk:
                        break
                    text = decoder.decode(chunk)
                    if text:
                        with lock:
                            if not output.closed:
                                output.write(text.encode('utf-8'))
                text = decoder.decode(b'', final=True)
                if text:
                    with lock:
                        if not output.closed:
                            output.write(text.encode('utf-8'))
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()

        def finish():
            timed_out = False
            while self._child.poll() is None:
                if time.monotonic() - start >= timeout:
                    self._child.kill()
                    timed_out = True
                    break
                time.sleep(0.005)
            code = self._child.wait()
            reader.join(0.3)
            self._reader_stop.set()
            reader.join(0.1)
            with lock:
                if timed_out:
                    output.write(f'\n[Command timed out after {timeout}s]'.encode('utf-8'))
                output.close()
            self.returncode = 124 if timed_out else code
            self.elapsed_ms = (time.monotonic() - start) * 1000
            self._done.set()

        threading.Thread(target=finish, daemon=True).start()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired('stardust-native', timeout)
        return self.returncode

    def kill(self):
        self._child.kill()
