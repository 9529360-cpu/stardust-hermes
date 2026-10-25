"""Closed facts suppress stale injected sentences without changing stored topics."""
from __future__ import annotations

import uuid

import pytest

from tools import fact_store as fs
from tools import topic_memory_store as ts


@pytest.fixture()
def home(tmp_path, monkeypatch):
    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    # The existing millisecond ID generator can collide during fast Linux
    # fixture writes; this suite exercises recall, with distinct fact identities.
    monkeypatch.setattr(fs, "_new_id", lambda: "f" + uuid.uuid4().hex)
    from tools import memory_tool
    monkeypatch.setattr(memory_tool, "get_builtin_memory_config",
                        lambda config=None: {"fact_pipeline": True})
    return tmp_path


def update(subject, old, new):
    row = fs.add_fact(subject, "state", old)
    assert row["success"]
    result = fs.update_fact(row["target_id"], content=new)
    assert result["success"]
    return result["target_id"]


@pytest.mark.parametrize("subject,old,new,value", [
    ("residence", "阿豪住在吉隆坡。", "阿豪住在米兰。", "吉隆坡"),
    ("job", "阿豪在银行做分析师。", "阿豪是独立开发者。", "银行"),
    ("editor", "阿豪用 VS Code。", "阿豪用 Cursor。", "VS Code"),
])
def test_mixed_topic_removes_old_sentence_only(home, subject, old, new, value):
    update(subject, old, new)
    assert ts.upsert_topic_summary(subject, old + "\n" + new + "\n每周整理笔记。", title=subject)["success"]
    path = ts.topic_memory_path()
    before = path.read_bytes()
    text = ts.format_topic_recall(ts.recall_topic_summaries(subject))
    assert value not in text
    assert new in text
    assert "每周整理笔记" in text
    assert path.read_bytes() == before


def test_unrelated_same_value_and_prefix_stay(home):
    update("phone", "阿豪用 Pixel 8。", "阿豪用 iPhone 17。")
    ts.upsert_topic_summary("phone-old", "阿豪用 Pixel 8。", title="phone")
    ts.upsert_topic_summary("photography", "Pixel 8 是摄影课的道具。", title="photography")
    ts.upsert_topic_summary("phone-case", "阿豪的 phone case 型号是 Pixel 80。", title="phone case")
    rows = ts.recall_topic_summaries("phone Pixel photography phone case", limit=10)
    by_key = {r["key"]: r["summary"] for r in rows}
    assert "phone-old" not in by_key
    assert "Pixel 8" in by_key["photography"]
    assert "Pixel 80" in by_key["phone-case"]


def test_same_subject_other_predicate_not_a_successor(home):
    old = fs.add_fact("person", "city", "阿豪住在罗马。", observed_at="2025-01-01T00:00:00+00:00")
    fs.close_fact(old["target_id"], reason="retracted")
    fs.add_fact("person", "job", "阿豪是工程师。")
    assert fs.closed_fact_supersessions() == []


def test_update_chain_follows_pointer_even_with_backdated_observation(home):
    first = fs.add_fact("city", "state", "阿豪住在罗马。", observed_at="2026-01-01T00:00:00+00:00")
    second = fs.update_fact(first["target_id"], content="阿豪住在都灵。", observed_at="2025-02-01T00:00:00+00:00")
    fs.update_fact(second["target_id"], content="阿豪住在米兰。", observed_at="2025-03-01T00:00:00+00:00")
    pairs = fs.closed_fact_supersessions()
    assert len(pairs) == 2
    assert all(p["current_contents"] == ["阿豪住在米兰。"] for p in pairs)


def test_english_sentences_keep_unrelated_current_content(home):
    update("work-computer", "User uses ThinkPad.", "User uses MacBook.")
    ts.upsert_topic_summary("work-computer", "User uses ThinkPad. User uses MacBook. Notes stay.",
                            title="work computer")
    text = ts.format_topic_recall(ts.recall_topic_summaries("work computer"))
    assert "ThinkPad" not in text
    assert "### work computer" in text
    assert "MacBook" in text
    assert "Notes stay." in text


def test_numeric_subjects_do_not_cross_suppress(home):
    update("person-1", "User lives in Rome.", "User lives in Milan.")
    ts.upsert_topic_summary("person-2", "User lives in Rome.", title="person-2")
    text = ts.format_topic_recall(ts.recall_topic_summaries("person-2"))
    assert "### person-2" in text
    assert "Rome" in text


def test_harness_distinguishes_outdated_annotation(home):
    from evals.digital_life.memory_retrieval import score_question
    ts.upsert_topic_summary("phone-old", "Pixel 8", title="Old phone")
    q = {"id": "test", "category": "outdated", "question": "phone?",
         "target_topic": "phone", "stale_topic": "phone-old"}
    stale = score_question(q, ["phone-old"], "### Old phone\nPixel 8", 0, len)
    marked = score_question(q, ["phone-old"],
                            "### Old phone\n[outdated, now: iPhone 17] Pixel 8", 0, len)
    assert stale["outdated_as_current"]
    assert not marked["outdated_as_current"]


def test_cache_separates_homes_and_topic_edits(home, monkeypatch):
    ts.upsert_topic_summary("editor", "阿豪用 VS Code。", title="editor")
    update("editor", "阿豪用 VS Code。", "阿豪用 Cursor。")
    assert "VS Code" not in ts.format_topic_recall(ts.recall_topic_summaries("editor"))
    ts.upsert_topic_summary("editor", "阿豪用 Cursor。\n保留新笔记。", title="editor")
    assert "保留新笔记" in ts.format_topic_recall(ts.recall_topic_summaries("editor"))
    other = home / "other"
    other.mkdir()
    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(other))
    ts.upsert_topic_summary("editor", "阿豪用 VS Code。", title="editor")
    assert "VS Code" in ts.format_topic_recall(ts.recall_topic_summaries("editor"))


def test_flag_off_and_cache_invalidation(home, monkeypatch):
    from tools import memory_tool
    ts.upsert_topic_summary("editor", "阿豪用 VS Code。", title="editor")
    before = ts.format_topic_recall(ts.recall_topic_summaries("editor"))
    new_id = update("editor", "阿豪用 VS Code。", "阿豪用 Cursor。")
    assert "VS Code" not in ts.format_topic_recall(ts.recall_topic_summaries("editor"))
    monkeypatch.setattr(memory_tool, "get_builtin_memory_config", lambda config=None: {})
    assert ts.format_topic_recall(ts.recall_topic_summaries("editor")) == before
    monkeypatch.setattr(memory_tool, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})
    fs.update_fact(new_id, content="阿豪用 VS Code。")
    assert "### editor" in ts.format_topic_recall(ts.recall_topic_summaries("editor"))
