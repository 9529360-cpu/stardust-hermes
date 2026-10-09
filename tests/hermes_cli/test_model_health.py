import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli.model_health import probe_model_health

KEY = "fake-health-key-not-a-secret"


@pytest.fixture
def endpoint():
    state = {"status": 200, "post_status": 200, "delay": 0, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            body = None
            if self.command == "POST":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.command, self.path, self.headers.get("Authorization"), body))
            time.sleep(state["delay"])
            self.send_response(state["post_status"] if self.command == "POST" else state["status"])
            self.end_headers()
            try:
                self.wfile.write(KEY.encode())  # malicious/credential-echoing provider
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = {"provider": "custom", "api_mode": "chat_completions", "api_key": KEY,
               "base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "test-model"}
    yield runtime, state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("status,kind", [(200, None), (401, "auth"), (403, "auth"),
                                         (429, "rate_limit"), (500, "server"), (400, "unknown")])
def test_http_status_and_redaction(endpoint, status, kind, caplog):
    runtime, state = endpoint
    state["status"] = status
    result = probe_model_health(runtime)
    assert result["live_ok"] is (status == 200)
    assert result["error_kind"] == kind
    assert result["latency_ms"] >= 0
    assert state["requests"][0][:3] == ("GET", "/v1/models", f"Bearer {KEY}")
    assert KEY not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("status,kind", [(200, None), (404, "not_found"), (401, "auth")])
def test_completion_fallback(endpoint, status, kind):
    runtime, state = endpoint
    state.update(status=404, post_status=status)
    result = probe_model_health(runtime)
    assert result["error_kind"] == kind
    assert result["live_ok"] is (status == 200)
    assert [(r[0], r[1]) for r in state["requests"]] == [
        ("GET", "/v1/models"), ("POST", "/v1/chat/completions")]
    body = state["requests"][1][3]
    assert body["max_tokens"] == 1
    assert body["model"] == "test-model"
    assert KEY not in json.dumps(result)


def test_timeout(endpoint):
    runtime, state = endpoint
    state["delay"] = 0.2
    result = probe_model_health(runtime, timeout_s=0.04)
    assert result["error_kind"] == "timeout"
    assert result["live_ok"] is False
    assert result["latency_ms"] < 200
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("changes", [{"provider": "anthropic", "api_mode": "anthropic_messages"},
                                    {"provider": "bedrock"}, {"command": "model-cli"}])
def test_unsupported_no_request(endpoint, changes):
    runtime, state = endpoint
    runtime.update(changes)
    assert probe_model_health(runtime) == {
        "live_ok": None, "reason": "live probe unsupported for provider"}
    assert state["requests"] == []


def test_network_failure(endpoint):
    runtime, _ = endpoint
    runtime["base_url"] = "http://127.0.0.1:0/v1"
    result = probe_model_health(runtime)
    assert result["error_kind"] == "network"
    assert KEY not in json.dumps(result)


def test_redirect_is_not_followed(endpoint):
    runtime, state = endpoint
    state["status"] = 302
    assert probe_model_health(runtime)["error_kind"] == "unknown"
    assert len(state["requests"]) == 1
