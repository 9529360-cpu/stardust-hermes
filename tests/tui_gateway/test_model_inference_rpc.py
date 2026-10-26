from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli import runtime_provider
from tui_gateway import server


@pytest.fixture(autouse=True)
def _close_probe_test_logging_handlers():
    yield
    import hermes_logging

    hermes_logging._reset_queued_handlers()


_FAKE_KEY = "rpc-model-test-fake-key"
_SESSION_ID = "rpc-model-test-session"
_PROBE_ID = "rpc-model-test-probe"
_SELECTED_MODEL = "rpc-selected-model"


class _Transport:
    def __init__(self):
        self._closed = False
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)
        return True

    def close(self):
        self._closed = True


def _bind(transport):
    return server.bind_transport(transport)


def _request(transport, method, params):
    token = _bind(transport)
    try:
        return server.handle_request({"jsonrpc": "2.0", "id": method, "method": method, "params": params})
    finally:
        server.reset_transport(token)


@pytest.fixture
def inference_service(monkeypatch):
    state = {"entered": threading.Event(), "release": threading.Event(), "requests": [], "hold": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            state["requests"].append((self.path, body))
            state["entered"].set()
            if state["hold"]:
                state["release"].wait(4)
            response = json.dumps({
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(response)
                self.wfile.flush()
            except OSError:
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}/v1"
    monkeypatch.setattr(runtime_provider, "load_config", lambda: {
        "custom_providers": [{
            "name": "probe-local", "base_url": base_url, "api_key": _FAKE_KEY,
            "model": _SELECTED_MODEL,
        }],
    })
    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: {
        "provider": "probe-local", "default": _SELECTED_MODEL,
    })
    yield state
    state["release"].set()
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def _session(monkeypatch, transport, *, profile_home=None):
    session = {"transport": transport, "profile_home": profile_home}
    monkeypatch.setattr(server, "_sessions", {_SESSION_ID: session})
    return session


def _params():
    return {
        "profile": "",
        "session_id": _SESSION_ID,
        "probe_id": _PROBE_ID,
        "provider": "probe-local",
        "model": _SELECTED_MODEL,
        "timeout_s": 5,
    }


def test_inference_rpc_is_explicit_bound_to_live_session_and_emits_one_selected_model_post(
    monkeypatch, inference_service, caplog,
):
    transport = _Transport()
    _session(monkeypatch, transport)

    result = _request(transport, "setup.inference_test", _params())

    assert result["result"]["status"] == "success"
    assert result["result"]["inference_ok"] is True
    assert result["result"]["provider"] == "custom"
    assert result["result"]["model"] == _SELECTED_MODEL
    assert len(inference_service["requests"]) == 1
    path, body = inference_service["requests"][0]
    assert path == "/v1/chat/completions"
    assert body["model"] == _SELECTED_MODEL
    assert body["stream"] is False
    assert body["max_tokens"] == 16
    assert "tools" not in body
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert _FAKE_KEY not in json.dumps(result) + caplog.text
    assert "http://127.0.0.1" not in caplog.text


def test_cancel_requires_the_same_transport_and_reports_confirmed_socket_abort(monkeypatch, inference_service):
    transport = _Transport()
    foreign_transport = _Transport()
    _session(monkeypatch, transport)
    inference_service["hold"] = True
    inference_result = {}

    def start_probe():
        inference_result["response"] = _request(transport, "setup.inference_test", _params())

    worker = threading.Thread(target=start_probe, daemon=True)
    worker.start()
    assert inference_service["entered"].wait(3)

    foreign = _request(foreign_transport, "setup.inference_test_cancel", {
        "profile": "", "session_id": _SESSION_ID, "probe_id": _PROBE_ID,
    })
    assert foreign["result"]["status"] == "forbidden"
    assert len(inference_service["requests"]) == 1

    canceled = _request(transport, "setup.inference_test_cancel", {
        "profile": "", "session_id": _SESSION_ID, "probe_id": _PROBE_ID,
    })
    assert canceled["result"]["status"] == "cancel_requested"
    assert canceled["result"]["cancel_supported"] is True
    inference_service["release"].set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert inference_result["response"]["result"]["status"] == "canceled"
    assert inference_result["response"]["result"]["inference_ok"] is None


def test_profile_mismatch_fails_closed_before_runtime_resolution(monkeypatch):
    transport = _Transport()
    _session(monkeypatch, transport, profile_home="D:/served-profile")
    called = []
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **_: called.append(True))
    params = _params()

    response = _request(transport, "setup.inference_test", params)

    assert response["error"]["code"] == 4001
    assert called == []


def test_deadline_starts_at_rpc_entry_before_owner_resolution(monkeypatch, inference_service):
    transport = _Transport()
    _session(monkeypatch, transport)
    now = [100.0]
    import tui_gateway.methods_model_inference as inference_methods
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    owner_entered = threading.Event()
    owner_release = threading.Event()
    rpc_returned = threading.Event()
    resolver_calls = []
    original_owner = server._owner

    def blocked_owner(rid, params):
        owner_entered.set()
        assert owner_release.wait(3)
        return original_owner(rid, params)

    monkeypatch.setattr(server, "_owner", blocked_owner)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: resolver_calls.append(kwargs),
    )
    rpc_response = {}

    def invoke_rpc():
        rpc_response["frame"] = _request(
            transport, "setup.inference_test", {**_params(), "timeout_s": 0.5}
        )
        rpc_returned.set()

    rpc_thread = threading.Thread(target=invoke_rpc, daemon=True)
    rpc_thread.start()
    probe = None
    try:
        assert owner_entered.wait(2)
        now[0] = 101.0
        assert rpc_returned.wait(2), "RPC did not return while owner resolution remained blocked"
        assert owner_release.is_set() is False
        result = rpc_response["frame"]["result"]
        assert result["status"] == "timed_out"
        assert result["inference_ok"] is False
        with server._methods_model_inference._probe_lock:
            probe = next(p for p in server._pending_owner_probes.values()
                         if p.probe_id == _PROBE_ID)
            assert probe.worker_done is False
            assert probe.session is None
        assert inference_service["requests"] == []
        owner_release.set()
        assert probe.worker_done_event.wait(2)
        assert probe.agent is None
        assert resolver_calls == []
        assert inference_service["requests"] == []
    finally:
        owner_release.set()
        rpc_thread.join(timeout=2)


def test_pending_owner_cancel_retains_bounded_admission_until_worker_exits(monkeypatch):
    transport = _Transport()
    second_transport = _Transport()
    first_session = {"transport": transport, "profile_home": None}
    second_session = {"transport": second_transport, "profile_home": None}
    monkeypatch.setattr(server, "_sessions", {
        _SESSION_ID: first_session,
        "second-session": second_session,
    })
    monkeypatch.setattr(server, "_probes", {})
    monkeypatch.setattr(server, "_pending_owner_probes", {})
    monkeypatch.setattr(server, "_MAX_ACTIVE_PROBES", 1)
    entered = threading.Event()
    release = threading.Event()
    owner_calls = []
    rpc_response = {}
    rpc_returned = threading.Event()
    original_owner = server._owner

    def blocked_owner(rid, params):
        owner_calls.append(params["session_id"])
        if params["session_id"] == _SESSION_ID:
            entered.set()
            assert release.wait(10)
        return original_owner(rid, params)

    monkeypatch.setattr(server, "_owner", blocked_owner)

    def start_first_probe():
        rpc_response["frame"] = _request(transport, "setup.inference_test", _params())
        rpc_returned.set()

    rpc_thread = threading.Thread(target=start_first_probe, daemon=True)
    rpc_thread.start()
    probe = None
    try:
        assert entered.wait(2)
        cancel_response = _request(transport, "setup.inference_test_cancel", {
            "profile": "", "session_id": _SESSION_ID, "probe_id": _PROBE_ID,
        })
        assert cancel_response["result"]["status"] == "cancel_requested"
        assert rpc_returned.wait(2)
        assert rpc_response["frame"]["result"]["status"] == "canceled"
        assert rpc_response["frame"]["result"]["inference_ok"] is None
        with server._methods_model_inference._probe_lock:
            probe = next(p for p in server._pending_owner_probes.values()
                         if p.probe_id == _PROBE_ID)
            assert probe.worker_done is False

        second = _request(second_transport, "setup.inference_test", {
            **_params(), "session_id": "second-session", "probe_id": "second-probe",
        })
        assert second["error"]["code"] == 4028
        assert owner_calls == [_SESSION_ID]
        assert probe.agent is None
    finally:
        release.set()
        rpc_thread.join(timeout=2)

    assert probe is not None
    assert probe.worker_done_event.wait(2)
    assert owner_calls == [_SESSION_ID]
    assert probe.agent is None


    transport = _Transport()
    _session(monkeypatch, transport)

    def fail_resolution(**_kwargs):
        raise RuntimeError("resolver failure containing private route details")

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", fail_resolution)
    response = _request(transport, "setup.inference_test", _params())

    assert response["result"]["status"] == "failed"
    assert response["result"]["inference_ok"] is False
    assert response["result"]["error_kind"] == "unknown"
    assert "private route details" not in json.dumps(response)


def test_absolute_deadline_covers_blocked_resolver_and_late_return_cannot_post(monkeypatch, inference_service):
    transport = _Transport()
    _session(monkeypatch, transport)
    original_resolve = runtime_provider.resolve_runtime_provider
    resolver_entered = threading.Event()
    resolver_release = threading.Event()

    def blocked_resolver(**kwargs):
        resolver_entered.set()
        assert resolver_release.wait(3)
        return original_resolve(**kwargs)

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", blocked_resolver)
    params = {**_params(), "timeout_s": 0.15}
    rpc_response = {}
    rpc_returned = threading.Event()

    def invoke_rpc():
        rpc_response["frame"] = _request(transport, "setup.inference_test", params)
        rpc_returned.set()

    rpc_thread = threading.Thread(target=invoke_rpc, daemon=True)
    rpc_thread.start()
    try:
        assert resolver_entered.wait(2)
        assert rpc_returned.wait(2)
        result = rpc_response["frame"]["result"]
        assert result["status"] == "timed_out"
        assert result["inference_ok"] is False
        with server._methods_model_inference._probe_lock:
            probe = next(p for p in server._methods_model_inference._probes.values() if p.probe_id == _PROBE_ID)
            assert probe.worker_done is False
        resolver_release.set()
        assert probe.worker_done_event.wait(2)
        assert probe.agent is None
        assert inference_service["requests"] == []
    finally:
        resolver_release.set()
        rpc_thread.join(timeout=2)


def test_absolute_deadline_covers_agent_construction_and_late_agent_cannot_dispatch(
    monkeypatch, inference_service,
):
    import run_agent

    transport = _Transport()
    _session(monkeypatch, transport)
    construction_entered = threading.Event()
    construction_release = threading.Event()

    class BlockingAgent:
        def __init__(self, **_kwargs):
            construction_entered.set()
            assert construction_release.wait(3)
            self._interrupt_requested = False

        def close(self):
            pass

    monkeypatch.setattr(run_agent, "AIAgent", BlockingAgent)
    params = {**_params(), "timeout_s": 0.15}
    rpc_response = {}
    rpc_returned = threading.Event()

    def invoke_rpc():
        rpc_response["frame"] = _request(transport, "setup.inference_test", params)
        rpc_returned.set()

    rpc_thread = threading.Thread(target=invoke_rpc, daemon=True)
    rpc_thread.start()
    try:
        assert construction_entered.wait(2)
        assert rpc_returned.wait(2)
        result = rpc_response["frame"]["result"]
        assert result["status"] == "timed_out"
        assert result["inference_ok"] is False
        with server._methods_model_inference._probe_lock:
            probe = next(p for p in server._methods_model_inference._probes.values() if p.probe_id == _PROBE_ID)
            assert probe.worker_done is False
        construction_release.set()
        assert probe.worker_done_event.wait(2)
        assert probe.agent is None
        assert inference_service["requests"] == []
    finally:
        construction_release.set()
        rpc_thread.join(timeout=2)


def test_cancel_during_resolver_prevents_late_provider_dispatch(monkeypatch, inference_service):
    transport = _Transport()
    _session(monkeypatch, transport)
    original_resolve = runtime_provider.resolve_runtime_provider
    resolver_entered = threading.Event()
    resolver_release = threading.Event()

    def blocked_resolver(**kwargs):
        resolver_entered.set()
        assert resolver_release.wait(3)
        return original_resolve(**kwargs)

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", blocked_resolver)
    rpc_response = {}
    rpc_returned = threading.Event()

    def invoke_rpc():
        rpc_response["frame"] = _request(transport, "setup.inference_test", _params())
        rpc_returned.set()

    rpc_thread = threading.Thread(target=invoke_rpc, daemon=True)
    rpc_thread.start()
    try:
        assert resolver_entered.wait(2)
        canceled = _request(transport, "setup.inference_test_cancel", {
            "profile": "", "session_id": _SESSION_ID, "probe_id": _PROBE_ID,
        })
        assert canceled["result"]["status"] == "cancel_requested"
        assert rpc_returned.wait(2)
        assert rpc_response["frame"]["result"]["status"] == "canceled"
        with server._methods_model_inference._probe_lock:
            probe = next(p for p in server._methods_model_inference._probes.values() if p.probe_id == _PROBE_ID)
        resolver_release.set()
        assert probe.worker_done_event.wait(2)
        assert inference_service["requests"] == []
    finally:
        resolver_release.set()
        rpc_thread.join(timeout=2)


def test_probe_becomes_inactive_at_original_deadline_before_watchdog_cleanup(monkeypatch):
    transport = _Transport()
    session = _session(monkeypatch, transport)
    import tui_gateway.methods_model_inference as inference_methods

    now = [100.0]
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    probe = inference_methods._Probe(
        transport, session, "default", _SESSION_ID, "deadline-guard",
        "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0,
    )

    assert inference_methods.time.monotonic() == 100.0
    assert probe.deadline == 101.0
    assert probe.is_active() is True
    now[0] = 101.0
    assert probe.is_active() is False
    assert probe.done_event.is_set()
    assert probe.snapshot()["status"] == "timed_out"
    assert probe.snapshot()["inference_ok"] is False



def test_response_received_completion_wins_over_late_cancel(monkeypatch):
    import tui_gateway.methods_model_inference as inference_methods
    from tui_gateway.methods_model_inference import _Probe

    now = [100.0]
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    transport = _Transport()
    session = _session(monkeypatch, transport)
    probe = _Probe(transport, session, "default", _SESSION_ID, "linearized-response",
                   "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0)
    probe.state = "dispatching"
    assert probe.response_received() is True
    assert probe.complete({"status": "success", "inference_ok": True}) is True
    now[0] = 101.0
    assert probe.cancel() == "not_running"
    assert probe.snapshot()["status"] == "success"


def test_cancel_without_confirmed_socket_abort_is_not_supported(monkeypatch):
    transport = _Transport()
    session = _session(monkeypatch, transport)
    from tui_gateway.methods_model_inference import _Probe

    active = _Probe(transport, session, "default", _SESSION_ID, "live-no-prune",
                    "probe-local", _SELECTED_MODEL, 5.0)
    active.state = "dispatching"

    class NoActiveSocketAgent:
        def hard_interrupt(self, *, tool_reason):
            return False

    active.agent = NoActiveSocketAgent()
    assert active.cancel() == "cancel_not_supported"
    assert active.response_received() is False
    assert active.complete({"status": "success", "inference_ok": True}) is False
    assert active.snapshot()["status"] == "cancel_not_supported"

    with server._methods_model_inference._probe_lock:
        server._methods_model_inference._probes[active.key] = active
        server._methods_model_inference._prune_probes()
        assert server._methods_model_inference._probes.get(active.key) is active
        server._methods_model_inference._probes.pop(active.key, None)


def test_cancel_after_deadline_before_watchdog_settles_timed_out(monkeypatch):
    transport = _Transport()
    session = _session(monkeypatch, transport)
    now = [100.0]
    import tui_gateway.methods_model_inference as inference_methods
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    from tui_gateway.methods_model_inference import _Probe

    probe = _Probe(
        transport, None, "default", _SESSION_ID, "late-cancel-after-deadline",
        "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0,
    )
    pending_key = (id(transport), _SESSION_ID, "default", "late-cancel-after-deadline")
    with server._probe_lock:
        server._pending_owner_probes[pending_key] = probe

    try:
        now[0] = 101.0
        response = _request(transport, "setup.inference_test_cancel", {
            "profile": "", "session_id": _SESSION_ID, "probe_id": "late-cancel-after-deadline",
        })
    finally:
        with server._probe_lock:
            server._pending_owner_probes.pop(pending_key, None)

    assert response["result"]["status"] == "not_running"
    assert probe.snapshot()["status"] == "timed_out"
    assert probe.snapshot()["inference_ok"] is False
    assert probe.done_event.is_set()
    assert probe.cancel_event.is_set()



def test_cancel_after_deadline_before_watchdog_lock_settles_timeout(monkeypatch):
    import tui_gateway.methods_model_inference as inference_methods
    from tui_gateway.methods_model_inference import _Probe

    now = [100.0]
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    transport = _Transport()
    probe = _Probe(
        transport, None, "default", _SESSION_ID, "deadline-lock-race",
        "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0,
    )
    lock_held = threading.Event()
    cancel_started = threading.Event()
    result = {}

    def cancel():
        cancel_started.set()
        result["status"] = probe.cancel()

    # Hold the exact state lock a delayed watchdog would contend on. The clock
    # crosses the original deadline before cancel is allowed to arbitrate.
    with probe.lock:
        lock_held.set()
        cancel_thread = threading.Thread(target=cancel, daemon=True)
        cancel_thread.start()
        assert lock_held.is_set()
        assert cancel_started.wait(2)
        now[0] = 101.0
    cancel_thread.join(timeout=2)

    assert not cancel_thread.is_alive()
    assert result["status"] == "not_running"
    assert probe.snapshot()["status"] == "timed_out"
    assert probe.snapshot()["inference_ok"] is False
    assert probe.done_event.is_set()
    assert probe.cancel_event.is_set()


def test_cancel_before_deadline_wins_over_watchdog(monkeypatch):
    import tui_gateway.methods_model_inference as inference_methods
    from tui_gateway.methods_model_inference import _Probe

    now = [100.0]
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    probe = _Probe(
        _Transport(), None, "default", _SESSION_ID, "cancel-before-deadline",
        "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0,
    )

    assert probe.cancel() == "cancel_requested"
    assert probe.snapshot()["status"] == "canceled"
    assert probe.snapshot()["inference_ok"] is None


def test_deadline_wins_when_abort_ack_arrives_after_deadline_before_watchdog(monkeypatch):
    import tui_gateway.methods_model_inference as inference_methods
    from tui_gateway.methods_model_inference import _Probe

    now = [100.0]
    monkeypatch.setattr(inference_methods.time, "monotonic", lambda: now[0])
    probe = _Probe(
        _Transport(), None, "default", _SESSION_ID, "late-abort-ack",
        "probe-local", _SELECTED_MODEL, 1.0, started_at=100.0,
    )
    probe.state = "dispatching"
    abort_entered = threading.Event()
    release_abort = threading.Event()

    class DelayedAbortAgent:
        def hard_interrupt(self, *, tool_reason):
            abort_entered.set()
            assert release_abort.wait(2)
            return True

    probe.agent = DelayedAbortAgent()
    result = {}
    cancel_thread = threading.Thread(
        target=lambda: result.setdefault("status", probe.cancel()), daemon=True,
    )
    cancel_thread.start()
    assert abort_entered.wait(2)
    now[0] = 101.0
    release_abort.set()
    cancel_thread.join(timeout=2)

    assert not cancel_thread.is_alive()
    assert result["status"] == "not_running"
    assert probe.snapshot()["status"] == "timed_out"
    assert probe.snapshot()["inference_ok"] is False
    assert probe.done_event.is_set()


def test_deadline_wins_while_cancel_waits_for_abort_ack(monkeypatch):
    transport = _Transport()
    session = _session(monkeypatch, transport)
    from tui_gateway.methods_model_inference import _Probe
    release_abort = threading.Event()
    abort_entered = threading.Event()

    class BlockingAbortAgent:
        def hard_interrupt(self, *, tool_reason):
            abort_entered.set()
            assert release_abort.wait(3)
            return False

    probe = _Probe(
        transport, session, "default", _SESSION_ID, "deadline-wins-cancel",
        "probe-local", _SELECTED_MODEL, 0.1,
    )
    probe.state = "dispatching"
    probe.agent = BlockingAbortAgent()

    cancel_result = {}
    cancel_thread = threading.Thread(target=lambda: cancel_result.setdefault("status", probe.cancel()), daemon=True)
    cancel_thread.start()
    assert abort_entered.wait(2)

    timeout_thread = threading.Thread(target=probe.timeout, daemon=True)
    timeout_thread.start()
    try:
        assert probe.done_event.wait(2), "the hard deadline waited for cancellation's abort acknowledgement"
        assert probe.snapshot()["status"] == "timed_out"
        release_abort.set()
        timeout_thread.join(timeout=2)
        cancel_thread.join(timeout=2)
    finally:
        release_abort.set()

    assert not timeout_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert cancel_result["status"] == "not_running"
    assert probe.snapshot()["status"] == "timed_out"



def test_cancel_without_confirmed_abort_returns_not_supported(monkeypatch):
    transport = _Transport()
    session = _session(monkeypatch, transport)
    from tui_gateway.methods_model_inference import _Probe

    probe = _Probe(
        transport, session, "default", _SESSION_ID, _PROBE_ID,
        "probe-local", _SELECTED_MODEL, 5.0,
    )
    probe.state = "dispatching"

    class Agent:
        _active_request_abort = lambda self, _reason: None

        def hard_interrupt(self, *, tool_reason):
            return False

    probe.agent = Agent()
    with server._methods_model_inference._probe_lock:
        server._methods_model_inference._probes[probe.key] = probe

    try:
        response = _request(transport, "setup.inference_test_cancel", {
            "profile": "", "session_id": _SESSION_ID, "probe_id": _PROBE_ID,
        })
    finally:
        with server._methods_model_inference._probe_lock:
            server._methods_model_inference._probes.pop(probe.key, None)

    assert response["result"]["status"] == "cancel_not_supported"
    assert response["result"]["cancel_supported"] is False
    assert "may continue until its timeout" in response["result"]["message"]
