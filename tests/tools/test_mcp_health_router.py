from types import SimpleNamespace

import pytest

from tools import mcp_tool as core
from tools import mcp_route_metrics as route_metrics
from tools.mcp_health_router import tool_health


@pytest.fixture(autouse=True)
def _isolated_mcp_health_state(monkeypatch):
    for name, value in {
        "_servers": {},
        "_server_error_counts": {},
        "_server_breaker_opened_at": {},
        "_server_errors_all_application": {},
        "_server_connect_retry_after": {},
        "_server_connect_failures": {},
        "_server_connecting": set(),
        "_lazy_server_configs": {},
        "_mcp_tool_server_names": {},
        "_server_tool_scopes": {},
        "_server_trust_levels": {},
        "_tool_read_only_hints": {},
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr("tools.mcp_tool_scope._resolve_server_key", lambda name, *a, **k: name)
    monkeypatch.setattr("tools.mcp_tool_scope._server_key", lambda name, *a, **k: name)
    route_metrics.clear()
    yield
    route_metrics.clear()


def _server(*, session=True, proven=True, suspect=None, reconnecting=False, recycled=False):
    return SimpleNamespace(
        session=object() if session else None,
        _session_proven=proven,
        _suspect_reason=suspect,
        _reconnecting=reconnecting,
        _is_recycled_stdio=lambda: recycled,
    )


def _map_tool(server="github"):
    tool = f"mcp__{server}__search"
    core._mcp_tool_server_names[tool] = server
    return tool


def test_healthy_live_server_scores_one():
    tool = _map_tool()
    core._servers["github"] = _server()
    health = tool_health(tool, now=100.0)
    assert health.status == "healthy"
    assert health.score == 1.0


def test_open_transport_breaker_is_strongly_deprioritized():
    tool = _map_tool()
    core._servers["github"] = _server()
    core._server_error_counts["github"] = core._CIRCUIT_BREAKER_THRESHOLD
    core._server_breaker_opened_at["github"] = 90.0

    health = tool_health(tool, now=100.0)
    assert health.status == "breaker_open"
    assert health.score < 0.1
    assert health.retry_in == 50


def test_application_breaker_is_deprioritized_but_less_than_transport_failure():
    tool = _map_tool()
    core._servers["github"] = _server()
    core._server_error_counts["github"] = core._CIRCUIT_BREAKER_THRESHOLD
    core._server_breaker_opened_at["github"] = 90.0
    core._server_errors_all_application["github"] = True

    health = tool_health(tool, now=100.0)
    assert health.status == "breaker_open_application"
    assert 0.1 < health.score < 0.5


def test_connect_cooldown_reports_retry_window():
    tool = _map_tool()
    core._server_connect_retry_after["github"] = 132.2

    health = tool_health(tool, now=100.0)
    assert health.status == "connect_cooldown"
    assert health.score < 0.1
    assert health.retry_in == 33


def test_lazy_manifest_stays_usable_with_small_startup_penalty():
    tool = _map_tool()
    core._lazy_server_configs["github"] = {"lazy": True}

    health = tool_health(tool, now=100.0)
    assert health.status == "lazy"
    assert 0.8 < health.score < 1.0


def test_half_open_server_can_recover_but_loses_to_healthy_peer():
    tool = _map_tool()
    core._servers["github"] = _server()
    core._server_error_counts["github"] = core._CIRCUIT_BREAKER_THRESHOLD
    core._server_breaker_opened_at["github"] = 1.0

    health = tool_health(tool, now=100.0)
    assert health.status == "half_open"
    assert 0.4 <= health.score < 0.8


def test_non_mcp_tool_has_no_health_record():
    assert tool_health("read_file", now=100.0) is None

def test_recent_failure_rate_deprioritizes_otherwise_healthy_server():
    tool = _map_tool()
    core._servers["github"] = _server()

    route_metrics.record_call("github", elapsed_seconds=0.2, success=False)
    route_metrics.record_call("github", elapsed_seconds=0.2, success=False)
    route_metrics.record_call("github", elapsed_seconds=0.2, success=False)
    route_metrics.record_call("github", elapsed_seconds=0.2, success=True)

    health = tool_health(tool)
    assert health.status == "route_degraded"
    assert health.score < 0.9
    assert health.samples == 4
    assert health.recent_success_rate < 0.75


def test_slow_recent_route_is_deprioritized_after_enough_samples():
    tool = _map_tool()
    core._servers["github"] = _server()

    for _ in range(4):
        route_metrics.record_call("github", elapsed_seconds=8.0, success=True)

    health = tool_health(tool)
    assert health.status == "slow"
    assert health.score < 0.9
    assert health.ewma_latency_ms == 8000


def test_one_slow_sample_does_not_change_routing_weight():
    tool = _map_tool()
    core._servers["github"] = _server()
    route_metrics.record_call("github", elapsed_seconds=20.0, success=True)

    health = tool_health(tool)
    assert health.status == "healthy"
    assert health.score == 1.0
    assert health.samples == 1


def test_untrusted_write_tool_gets_small_approval_friction_penalty():
    tool = _map_tool()
    core._servers["github"] = _server()
    core._server_trust_levels["github"] = core._TRUST_UNTRUSTED
    core._tool_read_only_hints["github"] = {tool: False}

    health = tool_health(tool)
    assert health.status == "approval_required"
    assert health.approval_required is True
    assert 0.9 < health.score < 1.0


def test_untrusted_read_only_tool_does_not_get_approval_penalty():
    tool = _map_tool()
    core._servers["github"] = _server()
    core._server_trust_levels["github"] = core._TRUST_UNTRUSTED
    core._tool_read_only_hints["github"] = {tool: True}

    health = tool_health(tool)
    assert health.status == "healthy"
    assert health.score == 1.0

