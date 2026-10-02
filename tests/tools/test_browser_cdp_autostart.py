"""A configured loopback CDP endpoint nobody serves gets Stardust's own debug browser started.

Field checkup 2026-10-01: browser.cdp_url pointed at 127.0.0.1:9222 (left by an earlier
`/browser connect`), that browser was closed, and every browser task first failed after a 30s
wait ("BU_CDP_URL ... unreachable") before the model launched Chrome by hand.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import browser_tool_cdp as bt_cdp


class _DevTools(BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_address[1]
        body = json.dumps({"webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/x"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def devtools():
    """Start a fake DevTools endpoint on a given port; all are shut down afterwards."""
    servers = []

    def start(port):
        server = ThreadingHTTPServer(("127.0.0.1", port), _DevTools)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_closed_loopback_browser_is_started_before_connecting(monkeypatch, devtools):
    from hermes_cli.browser_connect import ChromeDebugLaunch

    port, launched = _free_port(), []

    def launch(launch_port, system=None):
        launched.append(launch_port)
        devtools(launch_port)
        return ChromeDebugLaunch(launched=True)

    monkeypatch.setenv("BROWSER_CDP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr("hermes_cli.browser_connect.launch_chrome_debug", launch)

    assert bt_cdp._get_cdp_override() == f"ws://127.0.0.1:{port}/devtools/browser/x"
    assert launched == [port]


def test_a_running_browser_is_not_started_again(monkeypatch, devtools):
    port = _free_port()
    devtools(port)
    monkeypatch.setenv("BROWSER_CDP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr("hermes_cli.browser_connect.launch_chrome_debug",
                        lambda *_a, **_k: pytest.fail("a running browser was launched again"))

    assert bt_cdp._get_cdp_override() == f"ws://127.0.0.1:{port}/devtools/browser/x"


def test_a_remote_endpoint_is_never_started_here(monkeypatch):
    import requests

    def refuse(*_a, **_k):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setenv("BROWSER_CDP_URL", "http://example-host:9223")
    monkeypatch.setattr("requests.get", refuse)
    monkeypatch.setattr("hermes_cli.browser_connect.launch_chrome_debug",
                        lambda *_a, **_k: pytest.fail("a remote endpoint was launched locally"))

    assert bt_cdp._get_cdp_override() == "http://example-host:9223"
