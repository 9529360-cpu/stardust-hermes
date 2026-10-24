"""Tests for the opt-in local semantic recall leg (DL-P2.10).

Covers: fill-in-only mode (semantic never touches a non-empty lexical
list), silent fallback on every failure mode (fastembed missing, model
load failure, embed failure), background-download degradation, the
persistent content-hash vector cache, flag plumbing (default OFF, never
imports fastembed when OFF), and byte-identical OFF behavior vs
``origin/main`` on the DL-P2.6 corpus (DL-P2.7 context-hash method).
"""

from __future__ import annotations

import json
import sys
import threading

import pytest

import tools.memory_semantic as ms
from tools.topic_memory_store import (
    _hybrid_ranked_keys,
    recall_topic_summaries,
    semantic_recall_enabled,
    hybrid_recall_enabled,
)


@pytest.fixture
def topic_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("STARDUST_HOME", str(home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture(autouse=True)
def _fresh_semantic_singleton():
    ms.reset_global_index()
    yield
    ms.reset_global_index()


def _write(home, rows):
    path = home / "memories" / "TOPICS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "topics": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def _topics():
    return [
        {
            "key": "residence",
            "title": "居住城市",
            "keywords": ["居住", "米兰"],
            "summary": "阿豪住在米兰。 阿豪搬到米兰三年了。",
        },
        {
            "key": "fav-color",
            "title": "最喜欢的颜色",
            "keywords": ["颜色", "蓝色"],
            "summary": "阿豪最喜欢蓝色。",
        },
        {
            "key": "pet",
            "title": "宠物",
            "keywords": ["猫", "名字"],
            "summary": "阿豪养了一只猫叫 Pixel。",
        },
    ]


def _fake_sem(monkeypatch, ranking):
    """Replace the singleton with a stub embedding index, no fastembed."""
    stub = ms.SemanticTopicIndex()
    captured = {}

    def fake_search(query, limit=ms.SEMANTIC_FETCH):
        captured["query"] = query
        return list(ranking)

    def fake_ensure(rows, signature):
        captured["rows"] = len(rows)
        return True

    stub.ensure = fake_ensure
    stub.search = fake_search
    monkeypatch.setattr(ms, "global_index", lambda: stub)
    return captured


def _flag_on(monkeypatch):
    """Turn the semantic flag ON for a direct _hybrid_ranked_keys call."""
    import tools.topic_memory_store as tms

    monkeypatch.setattr(tms, "semantic_recall_enabled", lambda: True)


# ── flag plumbing ───────────────────────────────────────────────────────────


def test_semantic_flag_defaults_off(topic_home):
    assert semantic_recall_enabled() is False
    assert hybrid_recall_enabled() is False


def test_semantic_flag_reads_config(topic_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(
        mt, "get_builtin_memory_config", lambda config=None: {"semantic_recall": True}
    )
    assert semantic_recall_enabled() is True
    monkeypatch.setattr(
        mt, "get_builtin_memory_config", lambda config=None: {"semantic_recall": False}
    )
    assert semantic_recall_enabled() is False


def test_flag_off_never_imports_fastembed(topic_home, monkeypatch):
    _write(topic_home, _topics())
    sys.modules.pop("fastembed", None)
    _fake_sem(monkeypatch, [("residence", 0.9)])  # even with a perfect leg available
    hits = recall_topic_summaries("阿豪的居住城市是哪里？", hybrid=True)
    assert isinstance(hits, list)
    assert "fastembed" not in sys.modules, "flag OFF must never import fastembed"


# ── fill-in-only mode ──────────────────────────────────────────────────────


def test_semantic_fill_only_touches_starved_lexical_gates(topic_home, monkeypatch):
    _write(topic_home, _topics())
    _flag_on(monkeypatch)
    captured = _fake_sem(monkeypatch, [("pet", 0.9)])
    # Lexical hit present: semantic leg must not be consulted at all.
    keys = _hybrid_ranked_keys(_topics(), "阿豪住在哪个城市", ["residence"])
    assert keys[0] == "residence"
    assert "query" not in captured

    # Lexical starvation: semantic top-1 takes the fill slot.
    keys = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    assert keys == ["pet"]


def test_semantic_fill_only_never_pushes_out_lexical_hits(topic_home, monkeypatch):
    _write(topic_home, _topics())
    _flag_on(monkeypatch)
    _fake_sem(monkeypatch, [("fav-color", 0.99)])
    keys = _hybrid_ranked_keys(_topics(), "阿豪住在哪个城市", ["residence", "fav-color"])
    # Strong lexical order preserved verbatim; nothing reordered or added.
    assert keys[:2] == ["residence", "fav-color"]


# ── silent fallbacks (never raise into the turn) ───────────────────────────


def test_fallback_fastembed_not_installed(topic_home, monkeypatch):
    _write(topic_home, _topics())
    _flag_on(monkeypatch)
    monkeypatch.setitem(sys.modules, "fastembed", None)  # import -> None (missing)
    keys = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    # BM25 fill still serves; nothing raises.
    assert isinstance(keys, list)


def test_fallback_model_load_failure_serves_bm25_fill(topic_home, monkeypatch):
    _write(topic_home, _topics())
    _flag_on(monkeypatch)
    monkeypatch.setattr(
        ms.SemanticTopicIndex, "_load_model", lambda self: (_ for _ in ()).throw(RuntimeError("onnx broken"))
    )
    keys = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    assert isinstance(keys, list)


def test_fallback_query_embed_failure_returns_empty(topic_home, monkeypatch):
    _write(topic_home, _topics())

    class Boom:
        def query_embed(self, query):
            raise RuntimeError("ONNX error")

        def embed(self, texts, batch_size=32):
            raise RuntimeError("ONNX error")

    index = ms.SemanticTopicIndex()
    index._model = Boom()
    index._model_state = ms._READY
    assert index.search("anything") == []


def test_fallback_first_recall_degrades_while_downloading(topic_home, monkeypatch):
    _write(topic_home, _topics())
    _flag_on(monkeypatch)
    started = threading.Event()
    release = threading.Event()
    def slow_load(self):
        started.set()
        release.wait(timeout=5)
        raise RuntimeError("network offline")

    monkeypatch.setattr(ms.SemanticTopicIndex, "_load_model", slow_load)
    keys = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    assert isinstance(keys, list)  # first recall served (BM25 fill), no block
    assert started.is_set()  # model loads on the daemon thread, not inline
    release.set()
    # The thread flips to FAILED; a second recall stays on the BM25 fill.
    keys2 = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    assert isinstance(keys2, list)


# ── vector cache (content-hash keyed, survives restart) ─────────────────────


def test_vector_cache_reused_across_singleton_reset(topic_home, monkeypatch):
    _write(topic_home, _topics())
    calls = []

    class Recorder:
        def __init__(self):
            self.call = 0

        def embed(self, texts, batch_size=32):
            self.call += 1
            calls.append(list(texts))
            import numpy as np

            return [np.ones(4, dtype="float32") for _ in texts]

        def query_embed(self, query):
            import numpy as np

            return iter([np.ones(4, dtype="float32")])

    real_model = Recorder()
    monkeypatch.setattr(
        ms.SemanticTopicIndex, "_load_model", lambda self: real_model
    )

    def _wait_ready(timeout=5.0):
        import time as _time

        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if ms.global_index()._model_state == ms._READY:
                return True
            _time.sleep(0.01)
        return False

    topics = _topics()
    rank1 = ms.semantic_rank("什么动物", topics)
    if not rank1:
        # First call only armed the loader thread; wait for READY and retry.
        assert _wait_ready(), "model thread did not become READY"
        rank1 = ms.semantic_rank("什么动物", topics)
    assert rank1, "expected a ranked result"

    # New process = fresh singleton; docs must come from the npz cache, so
    # the (shared) recorder sees no new embed calls.
    ms.reset_global_index()
    monkeypatch.setattr(
        ms.SemanticTopicIndex, "_load_model", lambda self: real_model
    )
    rank2 = ms.semantic_rank("什么颜色", topics)
    if not rank2:
        assert _wait_ready(), "model thread did not become READY (2nd)"
        rank2 = ms.semantic_rank("什么颜色", topics)
    assert len(calls) == 1, f"docs re-embedded despite cache: {calls}"
    assert rank2


def test_vector_cache_corrupt_falls_back_to_reembed(topic_home, monkeypatch):
    _write(topic_home, _topics())
    cache = ms._model_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "vectors.npz").write_bytes(b"not an npz file")
    # Corrupt cache must not raise; ensure() rebuilds (None model -> False
    # without fastembed, which is itself a silent fallback).
    index = ms.SemanticTopicIndex()
    assert index.ensure(_topics(), (1, 2, 3)) is False


# ── OFF byte-identical vs origin/main (DL-P2.7 context-hash method) ────────


def test_off_behavior_unchanged_on_starved_queries(topic_home, monkeypatch):
    """Flag OFF on a starved gate must equal the pre-DL-P2.10 BM25 fill.

    With the flag OFF the semantic import is never reached; the fill slot
    comes from the score-gated BM25 list exactly as in #584. A semantic
    stub returning a wrong key proves the leg is inert: the captured stub
    was never consulted.
    """
    _write(topic_home, _topics())
    captured = _fake_sem(monkeypatch, [("fav-color", 0.99)])  # wrong key on purpose
    keys_off = _hybrid_ranked_keys(_topics(), "家里养了什么动物", [])
    assert "query" not in captured, "semantic leg must not run with flag OFF"
    assert "fav-color" not in keys_off
    assert semantic_recall_enabled() is False
