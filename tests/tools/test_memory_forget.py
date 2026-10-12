"""Forget reaches every store the owner keeps, and forgotten text stays out of recall.

Behavior contracts against a temp STARDUST_HOME: the memory tool's remove/replace across MEMORY.md,
USER.md and topic summaries; the learning-graph delete/edit path; the forgotten-record file that
filters session_search and topic recall. Nothing is written under a real home.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from agent import learning_mutations as lm
from hermes_state import SessionDB
from tools.memory_tool import MemoryStore, memory_tool
from tools.memory_tombstones import forgotten_path, record_forgotten
from tools.session_search_tool import session_search
from tools.topic_memory_store import (
    get_topic_summary,
    recall_topic_summaries,
    remove_topic_summary,
    reset_topic_summaries,
    upsert_topic_summary,
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    base = tmp_path / "home"
    monkeypatch.setenv("STARDUST_HOME", str(base))
    (base / "memories").mkdir(parents=True)
    return base


@pytest.fixture
def store(home):
    s = MemoryStore(memory_char_limit=500, user_char_limit=300)
    s.load_from_disk()
    return s


def _file(home: Path, name: str) -> Path:
    return home / "memories" / name


def _forgotten_texts(home: Path) -> list[str]:
    return [row["text"] for row in json.loads(_file(home, "forgotten.json").read_text(encoding="utf-8"))["forgotten"]]


def _run(store, **kwargs) -> dict:
    return json.loads(memory_tool(store=store, **kwargs))


# ── memory tool: exact path unchanged ────────────────────────────────────────

def test_exact_removal_keeps_its_response_shape(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("memory", "The owner wakes late")

    result = _run(store, action="remove", target="memory", old_text="green tea")

    assert set(result) == {"success", "done", "target", "usage", "entry_count", "message", "note"}
    assert result["success"] is True and result["message"] == "Entry removed."
    assert store._entries_for("memory") == ["The owner wakes late"]


# ── memory tool: cross-store forget ──────────────────────────────────────────

def test_memory_target_forget_reaches_a_topic_summary(store, home):
    upsert_topic_summary("atlas", "Atlas is the codename for the owner's project.", title="Atlas", keywords=["Atlas"])

    result = _run(store, action="remove", target="memory", old_text="Atlas")

    assert result["success"] is True
    assert result["removed_from"] == ["topic"]
    assert get_topic_summary("atlas") is None


def test_user_profile_entry_is_found_when_memory_was_requested(store, home):
    store.add("user", "Name: Alice")

    result = _run(store, action="remove", target="memory", old_text="alice")

    assert result["success"] is True
    assert result["removed_from"] == ["user"]
    assert store._entries_for("user") == []
    assert "Name: Alice" not in _file(home, "USER.md").read_text(encoding="utf-8")


def test_topic_forget_removes_only_the_matching_sentence(store, home):
    upsert_topic_summary("project", "Ship date is March. Codename is Atlas. Owner is Alice.",
                         title="Project", keywords=["release"])

    result = _run(store, action="remove", target="memory", old_text="codename is Atlas")

    assert result["removed_from"] == ["topic"]
    summary = get_topic_summary("project")["summary"]
    assert "Ship date is March." in summary and "Owner is Alice." in summary
    assert "Atlas" not in summary


def test_keyword_only_match_removes_the_keyword_and_keeps_the_topic(store, home):
    upsert_topic_summary("project", "Ship date is March.", title="Project", keywords=["Atlas", "release"])

    result = _run(store, action="remove", target="memory", old_text="atlas")

    assert result["removed_from"] == ["topic"]
    topic = get_topic_summary("project")
    assert topic["summary"] == "Ship date is March."
    assert topic["keywords"] == ["release"]


def test_replace_refuses_a_match_that_is_only_in_a_topic_title_or_keywords(store, home):
    upsert_topic_summary("project", "Ship date is March.", title="Project", keywords=["Atlas"])

    result = _run(store, action="replace", target="memory", old_text="atlas", content="Atlas is closed")

    assert result["success"] is False
    assert "title or keywords" in result["error"]
    assert get_topic_summary("project")["summary"] == "Ship date is March."


def test_several_matches_across_stores_are_all_removed_and_listed(store, home):
    store.add("memory", "Atlas launch is in March")
    upsert_topic_summary("budget", "Atlas budget is set. Office is in Berlin.", title="Budget")

    result = _run(store, action="remove", target="memory", old_text="atlas")

    assert result["success"] is True
    assert result["removed_from"] == ["memory", "topic"]
    assert {row["store"] for row in result["removed"]} == {"memory", "topic"}
    assert store._entries_for("memory") == []
    assert "Office is in Berlin." in get_topic_summary("budget")["summary"]


def test_not_found_lists_candidates_from_every_store(store, home):
    store.add("memory", "The owner wakes late")
    store.add("user", "Name: Alice")
    upsert_topic_summary("notes", "Ship notes for the desktop.", title="Notes", keywords=["desktop"])

    result = _run(store, action="remove", target="memory", old_text="zzz-missing-phrase")

    assert result["success"] is False
    assert "No entry matched" in result["error"]
    assert {row["store"] for row in result["candidates"]} == {"memory", "user", "topic"}
    assert all(len(row["preview"]) <= 81 for row in result["candidates"])


def test_replace_finds_the_entry_in_another_store(store, home):
    store.add("user", "Name: Alice")

    result = _run(store, action="replace", target="memory", old_text="alice", content="Name: Alicia")

    assert result["success"] is True
    assert result["replaced_in"] == "user"
    assert store._entries_for("user") == ["Name: Alicia"]


def test_replace_refuses_to_guess_between_several_matches(store, home):
    store.add("user", "Likes Atlas coffee")
    upsert_topic_summary("budget", "Atlas budget is set.", title="Budget")
    before = _file(home, "USER.md").read_bytes()

    result = _run(store, action="replace", target="memory", old_text="atlas", content="Atlas is closed")

    assert result["success"] is False
    assert "Multiple entries matched" in result["error"]
    assert _file(home, "USER.md").read_bytes() == before


def test_topic_replace_rewrites_only_the_matching_sentence(store, home):
    upsert_topic_summary("project", "Ship date is March. Codename is Atlas. Owner is Alice.", title="Project")

    result = _run(store, action="replace", target="memory", old_text="codename is Atlas", content="Codename is Borealis.")

    assert result["success"] is True
    assert result["replaced_in"] == "topic"
    summary = get_topic_summary("project")["summary"]
    assert "Ship date is March." in summary and "Owner is Alice." in summary
    assert "Borealis" in summary and "Atlas" not in summary


def test_topic_key_removal_keeps_its_response(home):
    upsert_topic_summary("atlas", "Atlas notes.", title="Atlas")

    result = json.loads(memory_tool(action="remove", target="topic", topic="atlas"))

    assert result == {"success": True, "target": "topic", "topic": "atlas", "message": "Topic summary removed"}
    assert remove_topic_summary("atlas")["success"] is False


def test_topic_remove_with_a_phrase_forgets_only_that_sentence(store, home):
    upsert_topic_summary("project", "Ship date is March. Codename is Atlas. Owner is Alice.", title="Project")

    result = _run(store, action="remove", target="topic", topic="project", old_text="codename is Atlas")

    assert result["success"] is True
    assert result["removed_from"] == ["topic"]
    summary = get_topic_summary("project")["summary"]
    assert "Ship date is March." in summary and "Owner is Alice." in summary
    assert "Atlas" not in summary


def test_topic_remove_with_a_phrase_missing_from_that_topic_searches_every_store(store, home):
    upsert_topic_summary("project", "Ship date is March.", title="Project")
    store.add("memory", "The owner's codename is Atlas")

    result = _run(store, action="remove", target="topic", topic="project", old_text="codename is Atlas")

    assert result["success"] is True
    assert result["removed_from"] == ["memory"]
    assert store._entries_for("memory") == []
    assert get_topic_summary("project")["summary"] == "Ship date is March."


def test_batch_remove_of_a_phrase_held_elsewhere_is_refused_before_writing(store, home):
    store.add("memory", "Keep this entry")
    upsert_topic_summary("budget", "Atlas budget is set.", title="Budget")

    result = _run(store, operations=[{"action": "remove", "old_text": "Atlas budget"}], target="memory")

    assert result["success"] is False
    assert "topic summaries" in result["error"]
    assert store._entries_for("memory") == ["Keep this entry"]


# ── learning graph goes through the store ────────────────────────────────────

def test_learning_graph_delete_goes_through_the_store_and_a_later_add_does_not_resurrect(store, home):
    store.add("memory", "The owner's codename is Atlas")
    store.add("memory", "The owner drinks green tea")

    assert lm.delete_node("memory:memory:0")["ok"] is True
    store.add("memory", "The owner visited Shanghai")

    text = _file(home, "MEMORY.md").read_text(encoding="utf-8")
    assert "Atlas" not in text
    assert "The owner drinks green tea" in text
    assert "The owner's codename is Atlas" in _forgotten_texts(home)


def test_learning_graph_delete_works_after_a_reset(store, home):
    store.add("memory", "The owner works on Stardust")
    MemoryStore.reset_target("memory")
    fresh = MemoryStore(memory_char_limit=500, user_char_limit=300)
    fresh.load_from_disk()
    fresh.add("memory", "The owner uses a Windows laptop")

    assert lm.delete_node("memory:memory:0")["ok"] is True
    assert "Windows laptop" not in _file(home, "MEMORY.md").read_text(encoding="utf-8")


def test_learning_graph_edit_refuses_a_noncanonical_file_and_keeps_its_bytes(home):
    path = _file(home, "MEMORY.md")
    path.write_text("alpha note\n§\nbeta note\n", encoding="utf-8")
    before = path.read_bytes()

    result = lm.edit_node("memory:memory:0", "alpha rewritten")

    assert result["ok"] is False
    assert path.read_bytes() == before


def test_learning_graph_edit_replaces_the_entry_in_place(home):
    _file(home, "MEMORY.md").write_text("alpha note\n§\nbeta note", encoding="utf-8")

    assert lm.edit_node("memory:memory:0", "alpha rewritten")["ok"] is True

    assert _file(home, "MEMORY.md").read_text(encoding="utf-8") == "alpha rewritten\n§\nbeta note"


# ── tombstones: record, refuse corruption, reset ─────────────────────────────

def test_tombstone_file_records_text_and_timestamp_inside_the_home(store, home):
    store.add("memory", "The owner drinks green tea")

    _run(store, action="remove", target="memory", old_text="green tea")

    path = forgotten_path()
    assert path == _file(home, "forgotten.json")
    assert str(path).startswith(str(home))
    record = json.loads(path.read_text(encoding="utf-8"))["forgotten"][0]
    assert record["text"] == "The owner drinks green tea"
    assert record["source"] == "memory"
    datetime.fromisoformat(record["forgotten_at"])


@pytest.mark.parametrize("damaged", [b"{broken", b"\xff\xfe\x00 not utf-8", b'{"forgotten": 3}'],
                         ids=("bad-json", "bad-utf8", "bad-payload"))
def test_damaged_forgotten_file_refuses_removal_before_erasing(store, home, damaged):
    store.add("memory", "The owner drinks green tea")
    _file(home, "forgotten.json").write_bytes(damaged)

    result = _run(store, action="remove", target="memory", old_text="green tea")

    assert result["success"] is False
    assert "forgotten.json" in result["error"]
    assert store._entries_for("memory") == ["The owner drinks green tea"]
    assert _file(home, "forgotten.json").read_bytes() == damaged


def test_reset_records_the_erased_entries_as_forgotten(store, home):
    store.add("memory", "The owner drinks green tea")

    MemoryStore.reset_target("memory")

    assert "The owner drinks green tea" in _forgotten_texts(home)


def test_topic_reset_records_the_erased_summaries_as_forgotten(home):
    upsert_topic_summary("atlas", "Atlas notes are kept here.", title="Atlas notes")

    assert reset_topic_summaries() is True

    assert "Atlas notes are kept here." in _forgotten_texts(home)
    assert get_topic_summary("atlas") is None


def test_replacing_an_entry_does_not_forget_the_old_text(store, home):
    store.add("memory", "The owner lives in Berlin")

    _run(store, action="replace", target="memory", old_text="Berlin", content="The owner lives in Munich")

    assert not _file(home, "forgotten.json").exists()


# ── recall filters ───────────────────────────────────────────────────────────

def test_forgotten_phrase_is_dropped_from_session_search(store, home, tmp_path):
    phrase = "The owner's codename is Atlas"
    store.add("memory", phrase)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s_forgotten", source="cli")
    db.append_message("s_forgotten", role="user", content=f"Reminder: {phrase}, keep it private.")
    db.create_session("s_other", source="cli")
    db.append_message("s_other", role="user", content="Atlas the rocket launch is scheduled for Friday.")
    assert {r["session_id"] for r in json.loads(session_search(query="Atlas", db=db))["results"]} == {
        "s_forgotten", "s_other"}

    _run(store, action="remove", target="memory", old_text=phrase)

    ids = {r["session_id"] for r in json.loads(session_search(query="Atlas", db=db))["results"]}
    assert ids == {"s_other"}


def test_forgotten_phrase_is_dropped_from_topic_recall(store, home):
    phrase = "The owner's favourite editor is Vim"
    upsert_topic_summary("editor-notes", f"Editor setup notes. {phrase}.", title="Editor notes", keywords=["Vim"])
    assert [t["key"] for t in recall_topic_summaries("Vim editor notes")] == ["editor-notes"]
    store.add("memory", phrase)

    _run(store, action="remove", target="memory", old_text=phrase)

    assert recall_topic_summaries("Vim editor notes") == []


def test_re_saving_a_forgotten_summary_says_recall_will_skip_it(home):
    phrase = "The owner's favourite editor is Vim"
    upsert_topic_summary("editor-notes", f"Editor setup notes. {phrase}.", title="Editor notes")
    assert record_forgotten([phrase], source="memory") is None

    resaved = upsert_topic_summary("editor-notes", f"Editor setup notes. {phrase}.", title="Editor notes")

    assert resaved["success"] is True
    assert "forgotten earlier" in resaved["warning"]
