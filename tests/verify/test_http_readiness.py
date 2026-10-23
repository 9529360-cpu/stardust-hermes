"""Real loopback readiness, including runner-owned process teardown."""
import json
import socket
import sys
import time

import psutil
import pytest

from agent.verify.environment import load_manifest, save_manifest
from agent.verify.recipes import Recipe
from agent.verify.runner import _poll_readiness, run_verify


@pytest.mark.parametrize("code,accepted,delay,expected", [
    (200, None, 0, True), (503, None, 0, True), (401, None, 0, True),
    (200, [200], 0, True), (503, [200], 0, False),
    (401, [200, 401], 0, True), (200, [200], 2, True),
])
def test_owned_http_server(tmp_path, code, accepted, delay, expected):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = tmp_path / "server.py"
    pidfile = tmp_path / "server.pid"
    script.write_text(
        "import http.server, os, pathlib, time\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        f"time.sleep({delay})\n"
        "class Handler(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        f"        self.send_response({code}); self.end_headers()\n"
        "    def log_message(self, *args): pass\n"
        f"http.server.HTTPServer(('127.0.0.1', {port}), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    raw = {"name": "loopback", "start": f'"{sys.executable}" "{script}"', "port": port}
    if accepted is not None:
        raw["readinessAcceptedStatuses"] = accepted
    recipe = Recipe.from_dict(raw)
    save_manifest(tmp_path, recipe)
    recipe = load_manifest(tmp_path)
    # Capture exact runner-owned processes so a regression cannot leave a server behind.
    parent = psutil.Process()
    previous = {p.pid for p in parent.children(recursive=True)}
    import threading

    def cleanup_owned():
        if pidfile.exists():
            try:
                owned = psutil.Process(int(pidfile.read_text()))
                owned.kill()
                owned.wait(3)
            except psutil.NoSuchProcess:
                pass
        for proc in reversed(parent.children(recursive=True)):
            if proc.pid not in previous:
                try:
                    proc.kill()
                    proc.wait(3)
                except psutil.NoSuchProcess:
                    pass

    watchdog_fired = threading.Event()

    def emergency_cleanup():
        watchdog_fired.set()
        cleanup_owned()

    watchdog = threading.Timer(6, emergency_cleanup)
    watchdog.start()
    try:
        result = run_verify(tmp_path, recipe, phases=("start",), ready_timeout=3.5 if delay else 1.5)
        assert not watchdog_fired.is_set(), "runner teardown blocked; watchdog cleaned owned server"
        r = result.readiness
        assert result.ok is expected
        assert r.status_code == code
        assert r.reachable is True
        assert r.healthy is (expected if accepted is not None else None)
        payload = r.to_dict()
        assert payload["reachable"] is True
        assert payload["healthy"] is r.healthy
        observation, = result.observations()
        assert observation["observation"] == ("http-health" if accepted is not None else "http-reachability")
        assert observation["status_code"] == code
        assert observation["reachable"] is True
        assert observation["healthy"] is r.healthy
        if not expected:
            assert r.error == "reachable but unhealthy (503)"
            assert 1.3 <= r.duration < 3
        if delay:
            assert r.duration >= delay
        pid = int(pidfile.read_text())
        # POSIX shells can leave a terminated descendant as a zombie until the
        # host reaps it. A zombie is exited, even though its PID still exists.
        exit_deadline = time.monotonic() + 2
        while True:
            try:
                server_process = psutil.Process(pid)
                exited = server_process.status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                exited = True
            if exited or time.monotonic() >= exit_deadline:
                break
            time.sleep(.02)
        assert exited, f"owned server {pid} survived teardown"
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0
        print(json.dumps({"code": code, "accepted": accepted, "delay": delay,
                          "ready": r.ready, "duration": round(r.duration, 2), "torn_down": True}))
    finally:
        watchdog.cancel()
        watchdog.join(4)
        cleanup_owned()


@pytest.mark.parametrize("key", ["readinessAcceptedStatuses", "readiness_accepted_statuses"])
def test_expectation_roundtrip(key):
    recipe = Recipe.from_dict({"name": "auth", key: [200, 401]})
    assert recipe.readiness_accepted_statuses == [200, 401]
    assert Recipe.from_dict(recipe.to_dict()) == recipe


@pytest.mark.parametrize("value", [[], [True], [99], [600], ["200"], "200"])
def test_invalid_expectation_cannot_silently_enable_legacy(value):
    with pytest.raises(ValueError, match="readiness.*statuses"):
        Recipe.from_dict({"name": "bad", "readinessAcceptedStatuses": value})


def test_request_and_sleep_respect_remaining_budget(tmp_path):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(1)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        started = time.monotonic()
        ready, status, error = _poll_readiness(f"http://127.0.0.1:{server.server_port}/", .15)
        assert time.monotonic() - started < .7
        assert not ready and status is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
