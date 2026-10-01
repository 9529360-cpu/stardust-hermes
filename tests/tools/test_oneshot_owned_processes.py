"""A one-shot run stops the background processes it still owns when it exits.

Field failure (2026-09-30, maintainer's Windows machine): a Kanban worker (``hermes chat -q ... -Q``)
started ``python -m http.server 8765`` in the background, reported the page as served and exited. The
server outlived it on a dead stdout pipe — still bound to its port hours later, failing every request.
``hermes -z`` (``agent.close()``) and delegated children already stop what they own; the quiet ``-q``
exit (``cli._finalize_single_query``) did not.
"""

import http.server
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
import threading
import time

import psutil
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _serve_scripted_model(command: str, tool_results: list, ready: Path):
    """Local chat-completions endpoint: start ``command`` in the background, answer once ``ready`` exists.

    Waiting for the server to come up mirrors a worker that starts and checks its preview server; it
    also keeps the stop clear of the spawn instant, when the shell may still be creating its child.
    """

    class Provider(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(404)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "messages" not in request:
                self.send_error(404)
                return
            results = [m for m in request["messages"] if m["role"] == "tool"]
            has_terminal = any(t.get("function", {}).get("name") == "terminal" for t in request.get("tools", []))
            message = {"role": "assistant", "content": "Preview server checked."}
            if has_terminal and not results:
                message.update(content=None, tool_calls=[{
                    "id": "call_server", "type": "function", "function": {
                        "name": "terminal", "arguments": json.dumps({"command": command, "background": True}),
                    },
                }])
            elif results and not tool_results:
                tool_results.extend(json.loads(m["content"]) for m in results)
                deadline = time.monotonic() + 30
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
            response = {
                "id": "chatcmpl-local", "object": "chat.completion", "created": 1, "model": "test-model",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
            content_type = "application/json"
            if request.get("stream"):
                response["object"] = "chat.completion.chunk"
                response["choices"][0]["delta"] = response["choices"][0].pop("message")
                for index, tool in enumerate(message.get("tool_calls", [])):
                    tool["index"] = index
                raw = ("data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.2)
    return False


def test_quiet_one_shot_exit_stops_the_server_it_started(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: custom\n  api_mode: chat_completions\n"
        "terminal:\n  env: local\n  oneshot_completion_wait_seconds: 5\n"
        "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n",
        encoding="utf-8",
    )
    pid_file = tmp_path / "server.pid"
    long_running = tmp_path / "server.py"
    long_running.write_text(textwrap.dedent('''
        import os, pathlib, sys, time
        pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(120)
    '''), encoding="utf-8")
    # The local terminal backend uses bash, including Git Bash on Windows.
    command = shlex.join(path.as_posix() for path in (Path(sys.executable), long_running, pid_file))
    tool_results: list = []
    model = _serve_scripted_model(command, tool_results, ready=pid_file)
    url = f"http://127.0.0.1:{model.server_port}/v1"
    env = {**os.environ, "HERMES_HOME": str(home), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
           "TERMINAL_CWD": str(tmp_path), "OPENAI_BASE_URL": url, "OPENAI_API_KEY": "local-test-only",
           "PYTHONPATH": str(REPO_ROOT)}
    server_pid = None
    try:
        producer = subprocess.run([
            sys.executable, "-c",
            "import cli; cli.main(query='Start the preview server and check it', quiet=True, "
            "oneshot=True, provider='custom', model='test-model', api_key='local-test-only', "
            f"base_url={url!r}, toolsets='terminal', max_turns=3, ignore_rules=True)",
        ], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert producer.returncode == 0, producer.stdout + producer.stderr
        assert "Preview server checked." in producer.stdout
        server_pid = int(pid_file.read_text(encoding="utf-8"))
        assert _wait_gone(server_pid, timeout=15), "the one-shot run left its background server running"
        # The model was told at start time, so it never reports the server as left running.
        assert len(tool_results) == 1, tool_results
        assert "one-shot run" in tool_results[0].get("finite_session_note", ""), tool_results
    finally:
        model.shutdown()
        model.server_close()
        if server_pid is not None and psutil.pid_exists(server_pid):
            psutil.Process(server_pid).kill()


@pytest.mark.windows_only
def test_a_descendant_still_holding_the_pipe_cannot_hang_poll_or_kill(tmp_path, monkeypatch):
    """A survivor holding the output pipe may cost the leak, never the caller's thread.

    Windows reads background output with a blocking ``read1()`` that holds the stream's buffer lock,
    so closing stdout from another thread (``_release_finished_handles``, reached from the orphaned-pipe
    reconcile in poll/wait and from kill) waited for that read — i.e. until the descendant exited.
    Here the shell backgrounds a sleeper that inherits the pipe and exits; no tree kill can reach it.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from tools.process_registry import ProcessRegistry

    pid_file = tmp_path / "escaped.pid"
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(textwrap.dedent('''
        import os, pathlib, sys, time
        pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(120)
    '''), encoding="utf-8")
    command = shlex.join(path.as_posix() for path in (Path(sys.executable), sleeper, pid_file)) + " & echo started"
    registry = ProcessRegistry()
    session = registry.spawn_local(command, cwd=str(tmp_path), task_id="escaped-pipe")
    escaped_pid = None
    try:
        deadline = time.monotonic() + 30
        while not (pid_file.exists() and session.process.poll() is not None) and time.monotonic() < deadline:
            time.sleep(0.1)
        escaped_pid = int(pid_file.read_text(encoding="utf-8"))
        assert session.process.poll() is not None, "the shell should have exited, leaving the sleeper on the pipe"

        for call in (lambda: registry.poll(session.id), lambda: registry.kill_process(session.id, source="test")):
            done = threading.Event()
            threading.Thread(target=lambda: (call(), done.set()), daemon=True).start()
            assert done.wait(30), "a registry call hung on the escaped descendant's pipe"
        assert registry.get(session.id).exited
    finally:
        if escaped_pid is not None and psutil.pid_exists(escaped_pid):
            psutil.Process(escaped_pid).kill()
