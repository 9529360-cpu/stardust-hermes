from types import SimpleNamespace

import pytest

from tools import mcp_route_metrics as metrics
from tools import mcp_tool_handlers as handlers


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.setattr("tools.mcp_tool_scope._resolve_server_key", lambda name, *a, **k: name)
    metrics.clear()
    yield
    metrics.clear()


def test_metrics_use_recent_ewma_and_keep_only_aggregates():
    metrics.record_call("srv", elapsed_seconds=0.1, success=True, now=10.0)
    metrics.record_call("srv", elapsed_seconds=0.5, success=False, now=11.0)
    snap = metrics.snapshot("srv", now=12.0)

    assert snap.samples == 2
    assert snap.successes == 1
    assert snap.failures == 1
    assert 0.0 < snap.recent_success_rate < 1.0
    assert 100 < snap.ewma_latency_ms < 500
    assert snap.last_failure_age_seconds == 1.0
    assert set(snap.as_dict()) <= {
        "samples",
        "recent_success_rate",
        "ewma_latency_ms",
        "last_failure_age_seconds",
    }


def test_dispatch_records_one_final_logical_success(monkeypatch):
    server = SimpleNamespace(mark_tool_call=lambda: None)
    monkeypatch.setattr(
        handlers._loop,
        "_run_on_mcp_loop",
        lambda call, timeout: '{"result":"ok"}',
    )

    out = handlers._dispatch(
        "srv",
        server,
        "tools/call x",
        lambda: None,
        10.0,
        (),
        lambda exc: None,
    )

    assert out == '{"result":"ok"}'
    snap = metrics.snapshot("srv", route_token=id(server))
    assert snap.samples == 1
    assert snap.successes == 1
    assert snap.failures == 0


def test_dispatch_records_structured_error_as_failure(monkeypatch):
    server = SimpleNamespace(mark_tool_call=lambda: None)
    monkeypatch.setattr(
        handlers._loop,
        "_run_on_mcp_loop",
        lambda call, timeout: '{"error":"bad args"}',
    )

    handlers._dispatch(
        "srv",
        server,
        "tools/call x",
        lambda: None,
        10.0,
        (),
        lambda exc: None,
    )

    snap = metrics.snapshot("srv", route_token=id(server))
    assert snap.samples == 1
    assert snap.failures == 1


def test_user_interrupt_does_not_penalize_route(monkeypatch):
    server = SimpleNamespace(mark_tool_call=lambda: None)

    def _interrupt(call, timeout):
        raise InterruptedError()

    monkeypatch.setattr(handlers._loop, "_run_on_mcp_loop", _interrupt)

    out = handlers._dispatch(
        "srv",
        server,
        "tools/call x",
        lambda: None,
        10.0,
        (),
        lambda exc: None,
    )

    assert "interrupted" in out.lower()
    assert metrics.snapshot("srv", route_token=id(server)) is None
