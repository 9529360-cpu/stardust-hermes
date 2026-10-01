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


def test_duplicate_url_keeps_date_evidence_from_later_provider(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([
        {"title": "A", "url": "https://a.example/page", "description": "a", "position": 1},
    ]))
    secondary = _Provider("secondary", _ok([
        {
            "title": "A",
            "url": "http://www.a.example/page",
            "description": "a2",
            "position": 1,
            "published_date": "2026-10-01",
        },
    ]))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    out = search_ensemble(primary, "topic", 5)

    assert out["data"]["web"][0]["published_date"] == "2026-10-01"


def test_cached_keyless_reroute_is_retried_as_original_provider(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    from tools.web_result_cache import search_memo

    search_memo.store("primary", "topic", 5, {
        "success": True,
        "data": {
            "served_by": "secondary",
            "web": [
                {"title": "stale", "url": "https://stale.example", "description": "", "position": 1},
            ],
        },
    })
    primary = _Provider("primary", _ok([
        {"title": "fresh", "url": "https://fresh.example", "description": "", "position": 1},
    ]))
    secondary = _Provider("secondary", _ok([
        {"title": "other", "url": "https://other.example", "description": "", "position": 1},
    ]))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    out = search_ensemble(primary, "topic", 5)

    assert primary.calls == 1
    assert any(row["title"] == "fresh" for row in out["data"]["web"])
    assert all(row["title"] != "stale" for row in out["data"]["web"])


def test_keyless_reroute_is_not_counted_as_independent_consensus(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["parallel"]},
    )
    exa = _Provider("exa", {
        "success": True,
        "data": {
            "served_by": "parallel",
            "web": [
                {"title": "A", "url": "https://a.example", "description": "a", "position": 1},
            ],
        },
    })
    parallel = _Provider("parallel", _ok([
        {"title": "B", "url": "https://b.example", "description": "b", "position": 1},
    ]))
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: parallel if name == "parallel" else None,
    )

    out = search_ensemble(exa, "topic", 5)

    assert out["success"] is True
    assert out["data"]["backends"] == ["parallel"]
    assert out["data"]["requested_backends"] == ["exa", "parallel"]
    assert out["data"]["rerouted_backends"] == {"exa": "parallel"}
    assert out["data"]["web"][0]["sources"] == ["parallel"]
    assert out["data"]["web"][0]["title"] == "B"
    # The rerouted Exa alias must not add a second RRF vote or replace the
    # direct Parallel request's evidence.
    assert len(out["data"]["web"]) == 1


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


def test_partial_failure_retries_only_failed_member_on_next_call(monkeypatch):
    monkeypatch.setattr(
        "tools.web_tools._load_web_config",
        lambda: {"search_ensemble_backends": ["secondary"]},
    )
    primary = _Provider("primary", _ok([
        {"title": "A", "url": "https://a.example", "description": "a", "position": 1},
    ]))

    class _Flaky(_Provider):
        def search(self, query, limit=5):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary")
            return _ok([
                {"title": "B", "url": "https://b.example", "description": "b", "position": 1},
            ])

    secondary = _Flaky("secondary")
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: secondary if name == "secondary" else None,
    )

    first = search_ensemble(primary, "topic", 5)
    second = search_ensemble(primary, "topic", 5)

    assert "secondary" in first["data"]["failed_backends"]
    assert "failed_backends" not in second["data"]
    assert primary.calls == 1
    assert secondary.calls == 2


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
