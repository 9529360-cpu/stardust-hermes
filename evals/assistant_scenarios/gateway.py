"""The desktop backend as a child process, spoken to over its newline-delimited JSON-RPC stdio wire.

This is the same dispatcher, session model, approval requests and completion notifications the
Stardust desktop reaches over WebSocket; only the transport differs. Server→client requests
(``approval``, ``clarify``, desktop read bridges …) are answered by ``on_request``; returning ``None``
sends an error response, which the backend treats as "no answer" and moves on immediately.
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

EventHandler = Callable[[Dict[str, Any], float], None]
RequestHandler = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]


class GatewayError(RuntimeError):
    pass


class GatewayProcess:
    def __init__(self, *, python: str, repo_root: Path, env: Dict[str, str], log_path: Path,
                 on_event: EventHandler, on_request: RequestHandler):
        self.python, self.repo_root, self.env, self.log_path = python, Path(repo_root), env, Path(log_path)
        self.on_event, self.on_request = on_event, on_request
        self._proc: Optional[subprocess.Popen] = None
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._responses: Dict[int, Dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._ready = False

    # ── lifecycle ────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _process(self) -> subprocess.Popen:
        if self._proc is None:
            raise GatewayError("backend was never started")
        return self._proc

    def start(self, timeout: float = 180.0) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Own process group, so kill() can take down everything the backend spawned.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        with open(self.log_path, "ab") as log:
            self._proc = subprocess.Popen([self.python, "-m", "tui_gateway.entry"], cwd=self.repo_root,
                                          env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                          creationflags=flags, start_new_session=os.name != "nt")
        threading.Thread(target=self._read_loop, name="exam-gateway-reader", daemon=True).start()
        if not self.wait_until(lambda: self._ready, timeout):
            self.kill()
            raise GatewayError(f"backend did not become ready within {timeout:.0f}s (see {self.log_path})")

    def kill(self) -> None:
        """Hard-kill the backend and everything it spawned — what a crash or power loss looks like."""
        if not self.alive:
            return
        proc = self._process()
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=30)

    def close(self, timeout: float = 15.0) -> None:
        if not self.alive:
            return
        proc = self._process()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            self.kill()

    # ── wire ─────────────────────────────────────────────────────────────
    def call(self, method: str, params: Dict[str, Any], timeout: float = 90.0) -> Dict[str, Any]:
        rid = next(self._ids)
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not self.wait_until(lambda: rid in self._responses or not self.alive, timeout):
            raise GatewayError(f"{method}: no response within {timeout:.0f}s")
        response = self._responses.pop(rid, None)
        if response is None:
            raise GatewayError(f"{method}: backend exited (see {self.log_path})")
        if "error" in response:
            raise GatewayError(f"{method}: {response['error'].get('message', response['error'])}")
        return response.get("result") or {}

    def wait_until(self, predicate: Callable[[], Any], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(min(remaining, 0.5))
        return True

    def _write(self, obj: Dict[str, Any]) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        stdin = self._process().stdin
        if stdin is None:
            raise GatewayError("backend stdin is not a pipe")
        with self._write_lock:
            try:
                stdin.write(data)
                stdin.flush()
            except (OSError, ValueError) as exc:
                raise GatewayError(f"backend stdin closed: {exc}") from exc

    def _read_loop(self) -> None:
        stdout = self._process().stdout
        if stdout is None:
            return
        for raw in iter(stdout.readline, b""):
            try:
                frame = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(frame, dict):
                self._dispatch(frame)
            with self._cond:
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def _dispatch(self, frame: Dict[str, Any]) -> None:
        method = frame.get("method")
        if method is None and isinstance(frame.get("id"), int):
            self._responses[frame["id"]] = frame
        elif method == "event":
            params = frame.get("params") or {}
            if params.get("type") == "gateway.ready":
                self._ready = True
            self.on_event(params, time.monotonic())
        elif method and "id" in frame:
            self._answer(frame["id"], method, frame.get("params") or {})

    def _answer(self, rid: Any, method: str, params: Dict[str, Any]) -> None:
        try:
            result = self.on_request(method, params)
        except Exception as exc:  # a broken policy must not wedge the backend's waiting thread
            result, error = None, f"exam request handler failed: {exc}"
        else:
            error = f"{method} is not available in the exam"
        reply = ({"jsonrpc": "2.0", "id": rid, "result": result} if result is not None
                 else {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": error}})
        try:
            self._write(reply)
        except GatewayError:
            pass  # backend already gone; the lifecycle owner reports that
