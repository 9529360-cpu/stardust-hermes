"""Recall-side fact sidecar tests (DL-P2.9): flag ON appends currently-valid
fact lines; flag OFF / empty store keeps recall byte-identical."""
from __future__ import annotations

import pytest

from tools import fact_store as fs
from tools.topic_memory_store import (
    format_topic_recall,
    recall_topic_summaries,
    upsert_topic_summary,
)


@pytest.fixture()
def topic_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(home))
    upsert_topic_summary("residence", "阿豪住在意大利米兰，家乡吉隆坡，在做 Stardust 项目。",
                         title="居住地", keywords=["居住", "米兰", "城市"])
    yield home


@pytest.fixture()
def flag_on(topic_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})


def _render(query):
    return format_topic_recall(recall_topic_summaries(query))


def test_flag_on_empty_store_changes_nothing(topic_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})
    baseline = _render("阿豪现在住在哪个城市？")
    assert "### 居住地" in baseline  # topics still served
    assert "Current facts" not in baseline


def test_flag_on_appends_current_fact_only(topic_home, flag_on):
    r1 = fs.add_fact("residence", "state", "阿豪住在罗马。")
    fs.update_fact(r1["target_id"], content="阿豪住在米兰。")
    text = _render("阿豪现在住在哪个城市？")
    assert "Current facts about the user" in text
    assert "阿豪住在米兰。" in text
    assert "罗马" not in text  # superseded value is history, never served


def test_superseded_fact_not_served(topic_home, flag_on):
    r1 = fs.add_fact("fav-food", "state", "阿豪最喜欢四川火锅。")
    fs.update_fact(r1["target_id"], content="阿豪现在更喜欢寿司。")
    text = _render("阿豪最喜欢吃什么？")
    assert "寿司" in text
    assert "火锅" not in text


def test_fact_never_displaces_topics(topic_home, flag_on):
    fs.add_fact("phone", "state", "阿豪用 iPhone 17。")
    selected = recall_topic_summaries("阿豪现在住在哪个城市？")
    topic_keys = [row["key"] for row in selected if not str(row["key"]).startswith("fact:")]
    assert topic_keys == ["residence"]  # the topic slots are untouched


def test_fact_only_query_still_serves_facts(topic_home, flag_on):
    """A query the topic store misses but a fact answers must return the fact."""
    fs.add_fact("keyboard", "state", "阿豪用 HHKB 键盘。")
    text = _render("阿豪用什么键盘？")
    assert "HHKB" in text


def test_history_queryable_explicitly(topic_home, flag_on):
    r1 = fs.add_fact("job", "state", "阿豪在银行工作。")
    fs.update_fact(r1["target_id"], content="阿豪是独立开发者。")
    history = fs.fact_history("job")
    assert len(history) == 2
    assert fs.load_facts() != fs.load_facts(include_history=True)


def test_flag_off_byte_identical(topic_home, monkeypatch):
    import tools.memory_tool as mt

    fs.add_fact("residence", "state", "阿豪住在米兰。")
    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": False})
    text_off = _render("阿豪现在住在哪个城市？")
    # Compare against a render with the store file temporarily hidden.
    path = fs.fact_store_path()
    moved = path.with_suffix(".hidden")
    path.rename(moved)
    try:
        text_no_store = _render("阿豪现在住在哪个城市？")
    finally:
        moved.rename(path)
    assert text_off == text_no_store
