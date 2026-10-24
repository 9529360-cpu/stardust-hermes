"""Tests for the opt-in hybrid BM25+lexical topic recall (DL-P2.8a+b)."""

from __future__ import annotations

import json

import pytest

from tools.memory_hybrid import (
    FTS_ADMIT,
    HybridTopicIndex,
    rrf_fuse,
    _bigram_tokens,
)
from tools.topic_memory_store import (
    hybrid_recall_enabled,
    recall_topic_summaries,
    topic_memory_path,
    upsert_topic_summary,
    _decay_same_domain_forks,
    _score,
    _score_with_summary,
    _signals,
)

@pytest.fixture
def topic_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("STARDUST_HOME", str(home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return home


def _write(home, rows):
    path = home / "memories" / "TOPICS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "topics": rows}, ensure_ascii=False), encoding="utf-8")
    return path


# ── step a: summary-body scoring ────────────────────────────────────────────


def test_summary_body_adds_score_below_title_weight(topic_home):
    topic = {
        "key": "residence",
        "title": "居住城市",
        "keywords": ["居住", "搬家"],
        "summary": "阿豪住在米兰。 阿豪搬到米兰三年了。",
    }
    query = "阿豪住在哪个城市米兰"
    signals = _signals(query)
    base = _score(query, signals, topic)
    with_summary = _score_with_summary(query, signals, topic)
    # 城市 appears only in title signals already counted by _score; the
    # summary adds 米兰-family bigrams at weight 1 (< weight-3 title hits).
    assert with_summary >= base + 1


def test_recall_hits_topic_only_mentioned_in_summary(topic_home):
    """The DL-P2.6 failure: 住在哪个城市 never matched the residence topic."""
    _write(
        topic_home,
        [
            {
                "key": "residence",
                "title": "居住城市",
                "keywords": ["居住", "搬家", "米兰"],
                "summary": "阿豪住在米兰。 阿豪搬到米兰三年了。 阿豪住在运河边的公寓。",
            },
            {
                "key": "birth",
                "title": "出生城市",
                "keywords": ["出生", "家乡", "槟城"],
                "summary": "阿豪出生在马来西亚槟城。",
            },
        ],
    )
    hits = recall_topic_summaries("阿豪现在住在哪个城市？", hybrid=True)
    assert hits and hits[0]["key"] == "residence"


def test_hybrid_flag_off_keeps_pregate_behavior(topic_home):
    _write(
        topic_home,
        [
            {
                "key": "residence",
                "title": "居住城市",
                "keywords": ["居住"],
                "summary": "阿豪住在米兰。",
            }
        ],
    )
    # Same query as above: without hybrid the summary-only signals stay below
    # the >=6 gate, matching origin/main behaviour byte-for-byte.
    assert recall_topic_summaries("阿豪现在住在哪个城市？", hybrid=False) == []


def test_same_domain_decay_demotes_older_fork(topic_home):
    _write(
        topic_home,
        [
            {
                "key": "phone",
                "title": "手机",
                "keywords": ["手机", "设备"],
                "summary": "阿豪现在用 iPhone 17。",
                "updated_at": "2026-10-20T10:00:00+00:00",
            },
            {
                "key": "phone-old",
                "title": "手机（旧）",
                "keywords": ["手机", "设备", "旧"],
                "summary": "阿豪的手机是 Pixel 8。",
                "updated_at": "2026-09-01T10:00:00+00:00",
            },
        ],
    )
    hits = recall_topic_summaries("阿豪现在用的是什么手机？", hybrid=True)
    keys = [row["key"] for row in hits]
    assert keys.index("phone") < keys.index("phone-old")


def test_same_domain_decay_skips_equal_timestamps(topic_home):
    """Bulk-written stores have equal timestamps: lexical order must hold."""
    rows = [
        {
            "key": "phone",
            "title": "手机",
            "keywords": ["手机", "设备"],
            "summary": "阿豪现在用 iPhone 17。",
            "updated_at": "2026-10-20T10:00:00+00:00",
        },
        {
            "key": "phone-old",
            "title": "手机（旧）",
            "keywords": ["手机", "设备", "旧"],
            "summary": "阿豪的手机是 Pixel 8。",
            "updated_at": "2026-10-20T10:00:00+00:00",
        },
    ]
    scored = [(30, rows[0]), (24, rows[1])]
    _decay_same_domain_forks(scored, {})
    # No recency signal -> no demotion.
    assert scored[0][1]["key"] == "phone" and scored[0][0] == 30


# ── step b: FTS index + RRF fusion ──────────────────────────────────────────


def test_bigram_tokens_expand_cjk_and_keep_latin():
    tokens = _bigram_tokens("阿豪用 VS Code 写代码")
    assert "阿豪" in tokens and "用 v" not in tokens
    assert "vs code" in tokens
    assert "代码" in tokens and "写代" in tokens


def test_rrf_fuse_prefers_agreement_and_breaks_ties_deterministically():
    fused = rrf_fuse([("a", 0.0), ("b", 0.0), ("c", 0.0)], [("b", -3.0), ("a", -2.0), ("d", -1.0)])
    keys = [k for k, _ in fused]
    # b and a appear in both lists -> top; deterministic tie-break by key.
    assert keys[0] == "b" and keys[1] == "a"
    assert keys == sorted(keys, key=lambda k: (-(fused[0][1]), k)) or keys.index("c") < keys.index("d")


def test_index_search_ranks_summary_only_match_first():
    rows = [
        {"key": "pet", "title": "宠物", "keywords": ["宠物"], "summary": "阿豪养了一只猫叫 Pixel。"},
        {"key": "job", "title": "职业", "keywords": ["职业"], "summary": "阿豪是后端工程师。"},
    ]
    index = HybridTopicIndex()
    assert index.ensure(rows, (1, 1, len(rows))) is True
    ranked = index.search("阿豪的猫叫什么名字")
    assert ranked and ranked[0][0] == "pet"


def test_index_rebuilds_on_signature_change():
    rows = [{"key": "a", "title": "甲", "keywords": [], "summary": "内容甲"}]
    index = HybridTopicIndex()
    index.ensure(rows, (1, 1, 1))
    assert [k for k, _ in index.search("内容甲")] == ["a"]
    rows.append({"key": "b", "title": "乙", "keywords": [], "summary": "内容乙"})
    index.ensure(rows, (2, 2, 2))
    assert {k for k, _ in index.search("内容")} >= {"a", "b"}


def test_index_unavailable_when_fts_missing(monkeypatch):
    import sqlite3

    def boom(self, rows):
        return None

    monkeypatch.setattr(HybridTopicIndex, "_build", boom)
    index = HybridTopicIndex()
    assert index.ensure([{"key": "a", "title": "t", "keywords": [], "summary": "s"}], (1, 1, 1)) is False
    assert index.search("anything") == []


def test_recall_falls_back_to_lexical_without_fts(topic_home, monkeypatch):
    _write(
        topic_home,
        [
            {
                "key": "residence",
                "title": "居住城市",
                "keywords": ["居住", "米兰"],
                "summary": "阿豪住在米兰。",
            }
        ],
    )
    monkeypatch.setattr(HybridTopicIndex, "_build", lambda self, rows: None)
    hits = recall_topic_summaries("阿豪的居住城市是哪里？", hybrid=True)
    assert hits and hits[0]["key"] == "residence"


# ── flag plumbing ───────────────────────────────────────────────────────────


def test_hybrid_flag_defaults_off_and_reads_config(topic_home, monkeypatch):
    assert hybrid_recall_enabled() is False

    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"hybrid_recall": True})
    assert hybrid_recall_enabled() is True
    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"hybrid_recall": False})
    assert hybrid_recall_enabled() is False


def test_empty_store_and_single_topic_recall(topic_home):
    assert recall_topic_summaries("任何问题", hybrid=True) == []
    _write(topic_home, [{"key": "only", "title": "唯一", "keywords": ["唯一"], "summary": "唯一的内容。"}])
    hits = recall_topic_summaries("唯一的内容是什么？", hybrid=True)
    assert [row["key"] for row in hits] == ["only"]


def test_fts_admit_bound_is_small():
    # Guard against accidentally widening the BM25-only fill.
    assert FTS_ADMIT <= 8
