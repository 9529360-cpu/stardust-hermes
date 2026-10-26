"""Selected-model inference probe: real runtime resolution, minimal request, safe errors and socket abort."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli import runtime_provider
from hermes_cli.model_inference import run_model_inference_test


@pytest.fixture(autouse=True)
def _close_probe_test_logging_handlers():
    yield
    import hermes_logging

    hermes_logging._reset_queued_handlers()


_FAKE_KEY = "probe-fake-key-do-not-leak"


@pytest.fixture
def model_service():
    state = {
        "status": 200,
        "content": "Stardust model test OK",
        "delay": 0.0,
        "hold": False,
        "entered": threading.Event(),
        "release": threading.Event(),
        "requests": [],
        "closed": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            state["requests"].append({
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            })
            state["entered"].set()
            if state["hold"]:
                state["release"].wait(4)
            elif state["delay"]:
                time.sleep(state["delay"])
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if state["status"] >= 400:
                response = json.dumps({"error": {"message": _FAKE_KEY}}).encode()
            else:
                response = json.dumps({
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": state["content"]}, "finish_reason": "stop"}],
                    "model": "server-default-must-not-win",
                }).encode()
            try:
                self.wfile.write(response)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                state["closed"].set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}/v1"
    state["release"].set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _resolve_runtime(monkeypatch, base_url, *, selected_model="probe-selected-model", extra_body=None):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    custom_provider = {
        "name": "probe-local",
        "base_url": base_url,
        "api_key": _FAKE_KEY,
        "model": selected_model,
    }
    if extra_body is not None:
        custom_provider["extra_body"] = dict(extra_body)
    monkeypatch.setattr(runtime_provider, "load_config", lambda: {
        "custom_providers": [custom_provider],
    })
    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: {
        "provider": "probe-local",
        "default": selected_model,
    })
    runtime = runtime_provider.resolve_runtime_provider(
        requested="probe-local", target_model=selected_model,
    )
    assert runtime["provider"] == "custom"
    assert runtime["api_mode"] == "chat_completions"
    assert runtime["base_url"] == base_url
    assert runtime["api_key"] == _FAKE_KEY
    return runtime


def test_probe_uses_resolved_runtime_and_one_minimal_selected_model_request(monkeypatch, model_service, caplog):
    state, base_url = model_service
    state["content"] = _FAKE_KEY
    runtime = _resolve_runtime(monkeypatch, base_url)

    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="probe-selected-model",
        timeout_s=4,
    )

    assert result["status"] == "success"
    assert result["inference_ok"] is True
    assert result["nonempty_response"] is True
    assert len(state["requests"]) == 1
    request = state["requests"][0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {_FAKE_KEY}"
    assert request["body"]["model"] == "probe-selected-model"
    assert request["body"]["stream"] is False
    assert request["body"]["max_tokens"] == 16
    assert "tools" not in request["body"]
    assert [item["role"] for item in request["body"]["messages"]] == ["system", "user"]
    assert request["body"]["messages"][-1]["content"] == "Reply with exactly: Stardust model test OK"
    assert _FAKE_KEY not in json.dumps(result) + caplog.text
    assert base_url not in caplog.text


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "server")],
)
def test_probe_classifies_http_errors_without_reflecting_remote_body(monkeypatch, model_service, status, kind, caplog):
    state, base_url = model_service
    state["status"] = status
    runtime = _resolve_runtime(monkeypatch, base_url)
    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="probe-selected-model",
        timeout_s=3,
    )

    assert result["status"] == "failed"
    assert result["inference_ok"] is False
    assert result["error_kind"] == kind
    assert len(state["requests"]) == 1
    assert _FAKE_KEY not in json.dumps(result) + caplog.text


def test_probe_uses_provider_builder_for_modern_token_field_and_profile_parameters(model_service):
    state, base_url = model_service
    runtime = {
        "provider": "openai",
        "requested_provider": "openai",
        "api_mode": "chat_completions",
        "base_url": base_url,
        "api_key": _FAKE_KEY,
        "request_overrides": {"extra_body": {"metadata": {"probe_profile_marker": "kept"}}},
    }

    result = run_model_inference_test(
        runtime,
        selected_provider="openai",
        selected_model="gpt-5.5",
        timeout_s=3,
    )

    assert result["status"] == "success"
    assert len(state["requests"]) == 1
    request = state["requests"][0]
    body = request["body"]
    assert request["path"] == "/v1/chat/completions"
    assert body["model"] == "gpt-5.5"
    assert body["max_completion_tokens"] == 16
    assert "max_tokens" not in body
    assert body["metadata"]["probe_profile_marker"] == "kept"
    assert "tools" not in body


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", [{"type": "function", "function": {"name": "injected_tool", "parameters": {"type": "object"}}}]),
        ("messages", [{"role": "user", "content": "injected history"}]),
        ("model", "provider-default-must-not-win"),
        ("stream", True),
        ("functions", [{"name": "injected_function", "parameters": {"type": "object"}}]),
        ("function_call", {"name": "injected_function"}),
        ("max_length", 4096),
    ],
)
def test_probe_rejects_reserved_profile_extra_body_fields_before_post(
    monkeypatch, model_service, field, value,
):
    state, base_url = model_service
    runtime = _resolve_runtime(monkeypatch, base_url, extra_body={field: value})

    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="probe-selected-model",
        timeout_s=3,
    )

    assert result["status"] == "unsupported"
    assert result["inference_ok"] is None
    assert state["requests"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("functions", [{"name": "injected_function", "parameters": {"type": "object"}}]),
        ("function_call", "auto"),
    ],
)
def test_probe_rejects_top_level_legacy_function_fields_from_provider_builder(
    monkeypatch, model_service, field, value,
):
    import run_agent

    state, base_url = model_service
    runtime = _resolve_runtime(monkeypatch, base_url)
    original_builder = run_agent.AIAgent._build_api_kwargs

    def builder_with_legacy_function_field(self, api_messages, tools_for_api=None):
        kwargs = original_builder(self, api_messages, tools_for_api)
        kwargs[field] = value
        return kwargs

    monkeypatch.setattr(run_agent.AIAgent, "_build_api_kwargs", builder_with_legacy_function_field)
    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="probe-selected-model",
        timeout_s=3,
    )

    assert result["status"] == "unsupported"
    assert result["inference_ok"] is None
    assert state["requests"] == []
def test_probe_projects_safe_config_extra_body_fields_and_bounds_nested_output_cap(
    monkeypatch, model_service,
):
    state, base_url = model_service
    runtime = _resolve_runtime(
        monkeypatch,
        base_url,
        selected_model="gpt-5.5",
        extra_body={
            "metadata": {"probe_profile_marker": "kept"},
            "max_completion_tokens": 2048,
            "max_new_tokens": 1024,
            "num_predict": 4096,
        },
    )

    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="gpt-5.5",
        timeout_s=3,
    )

    assert result["status"] == "success"
    assert len(state["requests"]) == 1
    body = state["requests"][0]["body"]
    assert body["model"] == "gpt-5.5"
    assert body["metadata"]["probe_profile_marker"] == "kept"
    assert body.get("max_tokens", 16) <= 16
    assert body.get("max_completion_tokens", 16) <= 16
    assert body.get("max_new_tokens", 16) <= 16
    assert body.get("num_predict", 16) <= 16
    assert body["stream"] is False
    assert "tools" not in body
    assert [message["role"] for message in body["messages"]] == [body["messages"][0]["role"], "user"]
    assert body["messages"][0]["role"] in {"system", "developer"}


def test_probe_timeout_is_bounded_and_does_not_retry(monkeypatch, model_service):
    state, base_url = model_service
    state["delay"] = 8.0
    runtime = _resolve_runtime(monkeypatch, base_url)
    started = time.monotonic()
    result = run_model_inference_test(
        runtime,
        selected_provider="probe-local",
        selected_model="probe-selected-model",
        timeout_s=5.0,
    )

    assert result["status"] == "timed_out"
    assert result["inference_ok"] is False
    assert result["error_kind"] == "timeout"
    assert time.monotonic() - started < 6.5
    assert len(state["requests"]) == 1


def test_probe_confirms_cancel_only_after_openai_wire_socket_is_shutdown(monkeypatch, model_service):
    state, base_url = model_service
    state["hold"] = True
    runtime = _resolve_runtime(monkeypatch, base_url)
    attached = {}
    result_box = {}

    def run():
        result_box["result"] = run_model_inference_test(
            runtime,
            selected_provider="probe-local",
            selected_model="probe-selected-model",
            timeout_s=3,
            on_agent=lambda agent: attached.setdefault("agent", agent),
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert state["entered"].wait(2), "the selected model request never reached the local service"
    agent = attached["agent"]
    assert agent.hard_interrupt(tool_reason="model test cancellation test") is True
    state["release"].set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert result_box["result"]["status"] == "canceled"
    assert result_box["result"]["inference_ok"] is None
    assert len(state["requests"]) == 1


def test_unsupported_api_mode_does_not_issue_request(model_service):
    state, base_url = model_service
    runtime = {
        "provider": "anthropic",
        "api_mode": "anthropic_messages",
        "base_url": base_url,
        "api_key": _FAKE_KEY,
    }
    result = run_model_inference_test(
        runtime,
        selected_provider="anthropic",
        selected_model="claude-test",
        timeout_s=3,
    )
    assert result["status"] == "unsupported"
    assert result["inference_ok"] is None
    assert state["requests"] == []
    assert _FAKE_KEY not in json.dumps(result)
