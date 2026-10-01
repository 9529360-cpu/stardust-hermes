from types import SimpleNamespace

import pytest

from tools import mcp_tool as core
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
    }.items():
        monkeypatch.setattr(core, name, value)
    monkeypatch.setattr("tools.mcp_tool_scope._resolve_server_key", lambda name, *a, **k: name)


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
