import json

import pytest

from tools import web_tools
from tools.web_result_cache import search_memo


class _Provider:
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows
        self.calls = 0

    def supports_search(self):
        return True

    def is_available(self):
        return True

    def is_keyless_available(self):
        return False

    def search(self, query, limit=5):
        self.calls += 1
        return {"success": True, "data": {"web": list(self.rows)[:limit]}}


@pytest.fixture(autouse=True)
def _cache():
    search_memo.clear()
    yield
    search_memo.clear()


def _wire(monkeypatch, config):
    primary = _Provider(
        "primary",
        [{"title": "Primary", "url": "https://primary.example/a", "description": "a"}],
    )
    secondary = _Provider(
        "secondary",
        [{"title": "Secondary", "url": "https://secondary.example/b", "description": "b"}],
    )
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: "primary")
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: dict(config))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: primary if name == "primary" else secondary if name == "secondary" else None,
    )
    return primary, secondary


def test_adaptive_simple_query_uses_only_primary(monkeypatch):
    primary, secondary = _wire(
        monkeypatch,
        {
            "search_strategy": "adaptive",
            "search_ensemble_backends": ["secondary"],
            "cache_enabled": False,
        },
    )

    out = json.loads(web_tools.web_search_tool("python dataclass syntax", limit=5))

    assert out["success"] is True
    assert out["data"]["search_mode"] == "single"
    assert out["data"]["search_plan"]["intent"] == "simple"
    assert primary.calls == 1
    assert secondary.calls == 0


@pytest.mark.parametrize(
    "query,intent",
    [
        ("latest Python security news", "news"),
        ("verify whether this claim is true", "verification"),
        ("deep research on MCP adoption", "deep_research"),
        ("Postgres vs MySQL comparison", "comparison"),
        ("核实这个消息是否属实", "verification"),
    ],
)
def test_adaptive_research_queries_use_configured_ensemble(monkeypatch, query, intent):
    primary, secondary = _wire(
        monkeypatch,
        {
            "search_strategy": "adaptive",
            "search_ensemble_backends": ["secondary"],
            "cache_enabled": False,
        },
    )

    out = json.loads(web_tools.web_search_tool(query, limit=5))

    assert out["success"] is True
    assert out["data"]["search_mode"] == "ensemble"
    assert out["data"]["search_plan"]["intent"] == intent
    assert out["data"]["backends"] == ["primary", "secondary"]
    assert primary.calls == 1
    assert secondary.calls == 1


def test_legacy_strategy_preserves_always_ensemble_when_extras_are_configured(monkeypatch):
    primary, secondary = _wire(
        monkeypatch,
        {
            "search_ensemble_backends": ["secondary"],
            "cache_enabled": False,
        },
    )

    out = json.loads(web_tools.web_search_tool("python dataclass syntax", limit=5))

    assert out["success"] is True
    assert out["data"]["search_mode"] == "ensemble"
    assert "search_plan" not in out["data"]
    assert primary.calls == 1
    assert secondary.calls == 1


def test_explicit_single_strategy_never_calls_extra_provider(monkeypatch):
    primary, secondary = _wire(
        monkeypatch,
        {
            "search_strategy": "single",
            "search_ensemble_backends": ["secondary"],
            "cache_enabled": False,
        },
    )

    out = json.loads(web_tools.web_search_tool("latest security news", limit=5))

    assert out["success"] is True
    assert primary.calls == 1
    assert secondary.calls == 0
