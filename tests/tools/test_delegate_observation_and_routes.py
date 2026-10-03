"""Parent observation and distinct member model routes share existing delegation ownership."""

import json
import weakref
from types import SimpleNamespace

import pytest

import tools.delegate_tool as delegate
from tools.delegate_tool_observation import TAIL_BYTES


def test_parent_inspects_recent_activity_but_foreign_parent_cannot(tmp_path):
    parent = SimpleParent()
    path = tmp_path / "activity.log"
    path.write_text("early\n" + "x" * TAIL_BYTES + "\nresearch completed; awaiting review", encoding="utf-8")
    child = SimpleNamespace(_delegate_parent_ref=weakref.ref(parent), _live_transcript_path=str(path))
    delegate._register_subagent({
        "subagent_id": "observe-child", "agent": child, "goal": "research", "status": "running",
        "model": "research-model", "tool_count": 4, "last_tool": "web_search",
    })
    try:
        result = json.loads(delegate.delegate_task(action="inspect", subagent_id="observe-child", parent_agent=parent))
        assert result["activity"]["available"] and result["activity"]["truncated"]
        assert result["activity"]["text"].endswith("research completed; awaiting review")
        assert "early" not in result["activity"]["text"]
        assert result["last_tool"] == "web_search" and result["tool_count"] == 4
        denied = json.loads(delegate.delegate_task(action="inspect", subagent_id="observe-child", parent_agent=SimpleParent()))
        assert "error" in denied and "activity" not in denied
        path.unlink()
        missing = json.loads(delegate.delegate_task(action="inspect", subagent_id="observe-child", parent_agent=parent))
        assert missing["activity"] == {"available": False, "text": "", "truncated": False}
    finally:
        delegate._unregister_subagent("observe-child")


class SimpleParent:
    pass


def bundle(model, provider=None):
    return {"model": model, "provider": provider, "base_url": None, "api_key": None, "api_mode": None}


def test_distinct_member_routes_do_not_share_provider_credentials(monkeypatch):
    resolved, built = [], []

    def resolve(cfg, parent):
        resolved.append(dict(cfg))
        return bundle(cfg["model"], cfg.get("provider"))

    def build(**kwargs):
        built.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(delegate, "_resolve_delegation_credentials", resolve)
    monkeypatch.setattr(delegate, "_build_child_preserving_parent_tools", build)
    cfg = {"model": "default", "provider": "old", "api_key": "old-secret", "base_url": "https://old.invalid",
           "request_overrides": {"headers": {"Authorization": "old-secret"}}}
    tasks = [{"goal": "develop", "model": "coder"}, {"goal": "research", "model": "researcher", "provider": "new"}]
    children, error = delegate._build_children(tasks, [None, None], bundle("default", "old"), top_role="leaf",
        max_iterations=10, parent_agent=SimpleParent(), routing_cfg=cfg, live_deleg_id=None, live_writers=[])
    assert error is None and len(children) == 2
    assert resolved[0]["model"] == "coder" and resolved[0]["api_key"] == "old-secret"
    assert resolved[1] == {"model": "researcher", "provider": "new"}
    assert [kwargs["model"] for kwargs in built] == ["coder", "researcher"]
    assert [kwargs["override_provider"] for kwargs in built] == ["old", "new"]
    assert cfg["model"] == "default" and cfg["api_key"] == "old-secret"


def test_bad_member_route_refuses_entire_batch_before_build(monkeypatch):
    built = []

    def resolve(cfg, parent):
        raise ValueError("configured provider unavailable")

    monkeypatch.setattr(delegate, "_resolve_delegation_credentials", resolve)
    monkeypatch.setattr(delegate, "_build_child_preserving_parent_tools", lambda **kwargs: built.append(kwargs))
    children, error = delegate._build_children(
        [{"goal": "first"}, {"goal": "second", "provider": "missing"}], [None, None], bundle("default"),
        top_role="leaf", max_iterations=10, parent_agent=SimpleParent(), routing_cfg={}, live_deleg_id=None, live_writers=[])
    assert children == [] and "unavailable" in error and not built


def test_member_service_resolves_real_config_without_default_secret(monkeypatch):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "custom_providers:\n  - name: research-service\n    base_url: https://research.invalid/v1\n"
        "    api_key: test-research-secret\n", encoding="utf-8")
    built = []

    def build(**kwargs):
        built.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(delegate, "_build_child_preserving_parent_tools", build)
    children, error = delegate._build_children(
        [{"goal": "research competitors", "provider": "research-service", "model": "research-model"}],
        [None], bundle("default"), top_role="leaf", max_iterations=10, parent_agent=SimpleParent(),
        routing_cfg={"api_key": "default-secret", "base_url": "https://default.invalid/v1"},
        live_deleg_id=None, live_writers=[])
    assert error is None and len(children) == 1
    assert built[0]["override_base_url"] == "https://research.invalid/v1"
    assert built[0]["override_api_key"] == "test-research-secret"
    assert built[0]["model"] == "research-model"


@pytest.mark.parametrize("route", [{"model": 1}, {"provider": " "}])
def test_invalid_member_route_is_rejected(route):
    from tools.delegate_tool_tasks import _normalize_task_list
    tasks, error = _normalize_task_list(None, None, [{"goal": "develop project", **route}], None, "leaf", 3)
    assert tasks is None and "non-empty string" in error
