import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tui_gateway import server


@pytest.mark.parametrize("status,kind", [(200, None), (401, "auth"), (500, "server")])
def test_live_runtime_rpc(monkeypatch, status, kind, caplog):
    key = "fake-rpc-health-key"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(key.encode())

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("hermes_cli.main._has_any_provider_configured", lambda **kw: True)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {
        "provider": "custom", "api_key": key, "model": "model-" + key,
        "source": "test", "base_url": f"http://127.0.0.1:{http.server_port}/v1"})
    try:
        default = server.handle_request({"id": "1", "method": "setup.runtime_check", "params": {}})
        assert default["result"]["ok"] is True
        assert "live_ok" not in default["result"]
        assert requests == []
        response = server.handle_request({"id": "2", "method": "setup.runtime_check",
                                          "params": {"live": True, "timeout_s": 1}})
        result = response["result"]
        assert result["ok"] is True  # credential readiness is backwards compatible
        assert result["live_ok"] is (status == 200)
        assert result["error_kind"] == kind
        assert result["latency_ms"] >= 0
        assert requests == ["/v1/models"]
        assert key not in json.dumps([default, response]) + caplog.text
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
