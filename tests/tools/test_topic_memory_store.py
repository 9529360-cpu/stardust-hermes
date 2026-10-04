"""Tests for profile-scoped topic summaries and local relevance recall."""

from __future__ import annotations

import json

import pytest

from tools.topic_memory_store import (
    get_topic_summary,
    recall_topic_summaries,
    remove_topic_summary,
    topic_memory_path,
    upsert_topic_summary,
)


@pytest.fixture
def topic_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_upsert_replaces_current_topic_instead_of_appending_history(topic_home):
    created = upsert_topic_summary(
        "stardust",
        "Stardust is the active desktop product.",
        title="Stardust",
        keywords=["星尘", "desktop"],
    )
    assert created["success"] is True

    updated = upsert_topic_summary(
        "stardust",
        "Stardust is the only active product line; ZN is archived.",
        title="Stardust",
        keywords=["星尘", "ZN", "desktop"],
    )
    assert updated["success"] is True

    payload = json.loads(topic_memory_path().read_text(encoding="utf-8"))
    assert len(payload["topics"]) == 1
    assert payload["topics"][0]["summary"] == "Stardust is the only active product line; ZN is archived."
    assert "active desktop product" not in payload["topics"][0]["summary"]


def test_recall_selects_relevant_chinese_topic_and_skips_unrelated(topic_home):
    assert upsert_topic_summary(
        "stardust", "任务页面在桌面工作区显示。", title="Stardust", keywords=["星尘", "任务", "桌面"]
    )["success"]
    assert upsert_topic_summary(
        "markus", "Markus 课堂只生成中文。", title="Markus 课堂", keywords=["Markus", "金融", "课堂"]
    )["success"]

    hit = recall_topic_summaries("星尘桌面的任务页面怎么处理？")
    assert [row["key"] for row in hit] == ["stardust"]
    assert recall_topic_summaries("今天天气怎么样？") == []


def test_remove_topic_deletes_only_that_summary(topic_home):
    upsert_topic_summary("stardust", "A", keywords=["星尘"])
    upsert_topic_summary("markus", "B", keywords=["Markus"])
    result = remove_topic_summary("stardust")
    assert result["success"] is True
    assert get_topic_summary("stardust") is None
    assert get_topic_summary("markus")["summary"] == "B"


def test_malformed_store_fails_closed_for_writes(topic_home):
    path = topic_memory_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not valid JSON"):
        upsert_topic_summary("stardust", "Do not overwrite unknown bytes.")
    assert path.read_text(encoding="utf-8") == "{broken"
