import pytest

from tools.web_result_cache import search_memo
from tools.web_search_ensemble import configured_ensemble_backends, search_ensemble


class _Provider:
    def __init__(self, name, response=None, *, error=None, available=True, keyless=False):
        self.name = name
        self._response = response
        self._error = error
        self._available = available
        self._keyless = keyless
        self.calls = 0

    def supports_search(self):
        return True

    def is_available(self):
        return self._available

    def is_keyless_available(self):
        return self._keyless

    def search(self, query, limit=5):
        self.calls += 1
        if self._error:
            raise self._error
        return self._response


def _ok(rows):
    return {"success": True, "data": {"web": rows}}


@pytest.fixture(autouse=True)
def _clear_search_cache():
    search_memo.clear()
    yield
    search_memo.clear()


def test_no_ensemble_config_keeps_single_provider_path(monkeypatch):
    monkeypatch.setattr("tools.web_tools._load_web_config", lambda: {})
    primary = _Provider("primary", _ok([]))
    assert configured_ensemble_backends("primary") == []
    assert search_ensemble(primary, "q", 5) is None
    assert primary.calls == 0


def test_rrf_merges_duplicate_urls_and_rewards_cross_engine_consensus(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([
        {"title": "A", "url": "https://a.example/page", "description": "a", "position": 1},
        {"title": "B", "url": "https://b.example/item?utm_source=one", "description": "b", "position": 2},
    ]))
    secondary = _Provider("secondary", _ok([
        {"title": "B better", "url": "http://www.b.example/item?utm_medium=two", "description": "better b", "position": 1},
        {"title": "C", "url": "https://c.example/", "description": "c", "position": 2},
    ]))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    out = search_ensemble(primary, "topic", 5)

    assert out["success"] is True
    assert out["data"]["search_mode"] == "ensemble"
    assert out["data"]["backends"] == ["primary", "secondary"]
    rows = out["data"]["web"]
    assert [row["title"] for row in rows] == ["B", "A", "C"]
    assert rows[0]["sources"] == ["primary", "secondary"]
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert primary.calls == 1
    assert secondary.calls == 1


def test_one_failed_backend_does_not_discard_successful_results(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([
        {"title": "A", "url": "https://a.example", "description": "a", "position": 1},
    ]))
    secondary = _Provider("secondary", error=RuntimeError("secondary down"))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    out = search_ensemble(primary, "topic", 5)

    assert out["success"] is True
    assert out["data"]["backends"] == ["primary"]
    assert "secondary" in out["data"]["failed_backends"]
    assert out["data"]["web"][0]["sources"] == ["primary"]


def test_unavailable_extra_provider_falls_back_to_normal_single_path(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([]))
    secondary = _Provider("secondary", _ok([]), available=False, keyless=False)
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    assert search_ensemble(primary, "topic", 5) is None
    assert primary.calls == 0
    assert secondary.calls == 0


def test_all_runtime_failures_return_structured_error(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", error=RuntimeError("primary down"))
    secondary = _Provider("secondary", error=RuntimeError("secondary down"))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    out = search_ensemble(primary, "topic", 5)

    assert out["success"] is False
    assert "primary" in out["error"]
    assert "secondary" in out["error"]


def test_fused_result_is_cached_as_ensemble(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([
        {"title": "A", "url": "https://a.example", "description": "a", "position": 1},
    ]))
    secondary = _Provider("secondary", _ok([
        {"title": "B", "url": "https://b.example", "description": "b", "position": 1},
    ]))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    first = search_ensemble(primary, "topic", 5)
    second = search_ensemble(primary, "topic", 5)

    assert first == second
    assert primary.calls == 1
    assert secondary.calls == 1


def test_ensemble_backend_list_is_deduped_bounded_and_excludes_primary(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {
            "search_ensemble_backends": [
                "primary", "a", "a", "b", "c", "d", "e", "f", "g",
            ]
        },
    )
    assert configured_ensemble_backends("primary") == ["a", "b", "c", "d", "e"]
