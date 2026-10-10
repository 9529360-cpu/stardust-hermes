"""cron.executions.list: read-only, profile-scoped, allowlisted cron runs."""
from unittest.mock import MagicMock, patch

import pytest

ALLOWED_ITEM_KEYS = {"id", "kind", "title", "status", "started_at", "updated_at", "detail"}
ALLOWED_DETAIL_KEYS = {"job_id", "source", "delivery_outcome", "error"}


@pytest.fixture()
def server(tmp_path):
    # Same import-only mock as test_protocol.py: the patch covers the first import of the gateway module.
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value=tmp_path / "hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")
    return mod


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    root = tmp_path / "hermes"
    monkeypatch.setattr("hermes_constants._get_platform_default_hermes_home", lambda: root)
    token = set_hermes_home_override(root)
    yield root
    reset_hermes_home_override(token)


def _runs(server, params=None):
    reply = server._methods["cron.executions.list"](1, params or {})
    assert "error" not in reply, reply
    return reply["result"]


def test_unknown_profile_is_rejected_not_served_from_the_launch_profile(server, home):
    assert "cron.executions.list" in server._methods
    reply = server._methods["cron.executions.list"](1, {"profile": "no-such-profile"})
    assert reply["error"]["code"] == 4064


def test_returns_allowlisted_runs_and_never_writes(server, home):
    from cron import executions

    record = executions.create_execution("job-1", source="scheduler")
    before = executions.list_executions(limit=500)

    result = _runs(server)

    assert result["scoped"] == ""
    item = result["work"][0]
    assert item["id"] == f"cron:{record['id']}"
    assert item["kind"] == "cron" and item["status"] == "running"
    assert set(item) <= ALLOWED_ITEM_KEYS
    assert set(item["detail"]) <= ALLOWED_DETAIL_KEYS
    assert executions.list_executions(limit=500) == before


def test_limit_is_clamped_to_one_through_fifty(server, home):
    from cron import executions

    for index in range(55):
        executions.create_execution(f"job-{index}", source="scheduler")

    assert len(_runs(server, {"limit": 1000})["work"]) == 50
    assert len(_runs(server, {"limit": -5})["work"]) == 1


def test_error_text_is_redacted_before_it_leaves_the_ledger(server, home):
    from cron import executions

    record = executions.create_execution("job-1", source="scheduler")
    executions.finish_execution(
        record["id"], success=False,
        error="provider unavailable Authorization: Bearer sk-proj-12345678901234567890",
    )

    work = _runs(server)["work"]

    assert work[0]["status"] == "failed"
    assert "sk-proj-12345678901234567890" not in work[0]["detail"]["error"]
