"""Forget reaches every store the owner keeps, and forgotten text stays out of recall and automatic writes.

Behavior contracts against a temp STARDUST_HOME: the memory tool's remove and replace across MEMORY.md, USER.md
and topic summaries; the learning-graph and owner-forget paths; the hashed forgotten-records file that filters
session_search and topic recall and refuses automatic re-adds; and the owner re-add that clears a record.
Nothing is written under a real home.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from agent import learning_mutations as lm
from hermes_state import SessionDB
from tools.memory_forget import forget_owner_entry
from tools.memory_tool import MemoryStore, apply_memory_pending, memory_tool
from tools.memory_tombstones import fold_text, forgotten_index, forgotten_path, record_forgotten
from tools.session_search_tool import session_search
from tools.skill_provenance import reset_current_write_origin, set_current_write_origin
from tools.topic_memory_store import (
    get_topic_summary,
    recall_topic_summaries,
    remove_topic_summary,
    reset_topic_summaries,
    topic_memory_path,
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


def _records(home: Path) -> list[dict]:
    return json.loads(_file(home, "forgotten.json").read_text(encoding="utf-8"))["forgotten"]


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(fold_text(text).encode("utf-8")).hexdigest()


def _run(store, **kwargs) -> dict:
    return json.loads(memory_tool(store=store, **kwargs))


def _background(fn):
    token = set_current_write_origin("background_review")
    try:
        return fn()
    finally:
        reset_current_write_origin(token)


# ── exact path and the sweep over every store ────────────────────────────────

def test_exact_removal_keeps_its_response_shape_and_names_its_store(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("memory", "The owner wakes late")

    result = _run(store, action="remove", target="memory", old_text="green tea")

    assert set(result) == {"success", "done", "target", "usage", "entry_count", "message", "note",
                           "removed_from", "removed"}
    assert result["success"] is True and result["message"] == "Entry removed."
    assert result["removed_from"] == ["memory"]
    assert store._entries_for("memory") == ["The owner wakes late"]


def test_exact_forget_sweeps_the_same_text_from_every_store(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("user", "The owner drinks green tea")
    upsert_topic_summary("tea-notes", "Morning routine. The owner drinks green tea. Ship date is March.",
                         title="Tea notes", keywords=["tea"])

    result = _run(store, action="remove", target="memory", old_text="The owner drinks green tea")

    assert result["removed_from"] == ["memory", "user", "topic"]
    assert store._entries_for("memory") == []
    assert store._entries_for("user") == []
    assert get_topic_summary("tea-notes")["summary"] == "Morning routine. Ship date is March."


def test_exact_forget_leaves_other_entries_and_topics_alone(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("memory", "The owner wakes late")
    upsert_topic_summary("project", "Ship date is March.", title="Project")

    result = _run(store, action="remove", target="memory", old_text="The owner drinks green tea")

    assert result["removed_from"] == ["memory"]
    assert store._entries_for("memory") == ["The owner wakes late"]
    assert get_topic_summary("project")["summary"] == "Ship date is March."


def test_forget_response_never_carries_the_forgotten_text(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    upsert_topic_summary("tea-notes", f"Morning routine. {phrase}.", title="Tea notes")

    raw = memory_tool(action="remove", target="memory", old_text=phrase, store=store)

    assert json.loads(raw)["removed_from"] == ["memory", "topic"]
    assert phrase not in raw


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


def test_several_matches_across_stores_are_all_removed_and_listed(store, home):
    store.add("memory", "Atlas launch is in March")
    upsert_topic_summary("budget", "Atlas budget is set. Office is in Berlin.", title="Budget")

    result = _run(store, action="remove", target="memory", old_text="atlas")

    assert result["success"] is True
    assert result["removed_from"] == ["memory", "topic"]
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

    result = _run(store, action="replace", target="memory", old_text="codename is Atlas",
                  content="Codename is Borealis.")

    assert result["success"] is True
    assert result["replaced_in"] == "topic"
    summary = get_topic_summary("project")["summary"]
    assert "Ship date is March." in summary and "Owner is Alice." in summary
    assert "Borealis" in summary and "Atlas" not in summary


def test_replace_refuses_a_match_that_is_only_in_a_topic_title_or_keywords(store, home):
    upsert_topic_summary("project", "Ship date is March.", title="Project", keywords=["Atlas"])

    result = _run(store, action="replace", target="memory", old_text="atlas", content="Atlas is closed")

    assert result["success"] is False
    assert "title or keywords" in result["error"]
    assert get_topic_summary("project")["summary"] == "Ship date is March."


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


# ── learning graph and owner forget ──────────────────────────────────────────

def test_learning_graph_delete_goes_through_the_store_and_a_later_add_does_not_resurrect(store, home):
    store.add("memory", "The owner's codename is Atlas")
    store.add("memory", "The owner drinks green tea")

    assert lm.delete_node("memory:memory:0")["ok"] is True
    store.add("memory", "The owner visited Shanghai")

    text = _file(home, "MEMORY.md").read_text(encoding="utf-8")
    assert "Atlas" not in text
    assert "The owner drinks green tea" in text
    assert _digest("The owner's codename is Atlas") in {row["sha256"] for row in _records(home)}


def test_learning_graph_delete_sweeps_the_same_text_from_other_stores(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("memory", "The owner wakes late")
    upsert_topic_summary("tea-notes", "Morning routine. The owner drinks green tea. Ship date is March.",
                         title="Tea notes")

    assert lm.delete_node("memory:memory:0")["ok"] is True

    memory_text = _file(home, "MEMORY.md").read_text(encoding="utf-8")
    assert "green tea" not in memory_text
    assert "The owner wakes late" in memory_text
    assert get_topic_summary("tea-notes")["summary"] == "Morning routine. Ship date is March."


def test_learning_graph_delete_works_after_a_reset(store, home):
    store.add("memory", "The owner works on Stardust")
    MemoryStore.reset_target("memory")
    fresh = MemoryStore(memory_char_limit=500, user_char_limit=300)
    fresh.load_from_disk()
    fresh.add("memory", "The owner uses a Windows laptop")

    assert lm.delete_node("memory:memory:0")["ok"] is True
    assert "Windows laptop" not in _file(home, "MEMORY.md").read_text(encoding="utf-8")


def test_owner_forget_entry_refuses_a_stale_index_and_changes_nothing(store, home):
    store.add("memory", "The owner wakes late")
    store.add("memory", "The owner drinks green tea")
    before = _file(home, "MEMORY.md").read_bytes()

    result = forget_owner_entry(store, "memory", "The owner drinks green tea", index=0)

    assert result["success"] is False
    assert _file(home, "MEMORY.md").read_bytes() == before


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


# ── forgotten records: hashes only ───────────────────────────────────────────

def test_forgotten_file_stores_a_hash_and_never_the_text(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)

    memory_tool(action="remove", target="memory", old_text="green tea", store=store)

    raw = _file(home, "forgotten.json").read_text(encoding="utf-8")
    assert "green tea" not in raw and "owner drinks" not in raw
    record = _records(home)[0]
    assert record["sha256"] == _digest(phrase)
    assert record["length"] == len(fold_text(phrase))
    assert record["source"] == "memory"
    datetime.fromisoformat(record["forgotten_at"])
    assert forgotten_path() == _file(home, "forgotten.json")


def test_topic_forget_records_the_topic_key_with_the_hash(store, home):
    upsert_topic_summary("project", "Ship date is March. Codename is Atlas.", title="Project")

    _run(store, action="remove", target="topic", topic="project", old_text="codename is Atlas")

    records = _records(home)
    assert records[0]["topic"] == "project"
    assert records[0]["sha256"] == _digest("Codename is Atlas.")
    assert "Codename" not in _file(home, "forgotten.json").read_text(encoding="utf-8")


def test_legacy_plaintext_records_still_filter_and_are_dropped_on_the_next_write(store, home):
    phrase = "The owner's codename is Atlas"
    _file(home, "forgotten.json").write_text(json.dumps({"version": 1, "forgotten": [
        {"text": phrase, "forgotten_at": "2026-10-01T00:00:00+00:00", "source": "memory"}]}), encoding="utf-8")
    assert forgotten_index().mentions(f"Reminder: {phrase}, keep it private.")

    store.add("memory", "The owner wakes late")
    _run(store, action="remove", target="memory", old_text="wakes late")

    raw = _file(home, "forgotten.json").read_text(encoding="utf-8")
    assert phrase not in raw and '"text"' not in raw
    assert _digest(phrase) in {row["sha256"] for row in _records(home)}


def test_short_phrase_is_recorded_but_never_filters(home):
    assert record_forgotten(["ok"], source="memory") is None

    assert _records(home)[0]["length"] == 2
    assert not forgotten_index().mentions("ok then, thanks")


def test_reset_records_the_erased_entries_as_hashes(store, home):
    store.add("memory", "The owner drinks green tea")

    MemoryStore.reset_target("memory")

    assert _digest("The owner drinks green tea") in {row["sha256"] for row in _records(home)}
    assert "green tea" not in _file(home, "forgotten.json").read_text(encoding="utf-8")


def test_topic_reset_records_the_erased_summaries_as_hashes(home):
    upsert_topic_summary("atlas", "Atlas notes are kept here.", title="Atlas notes")

    assert reset_topic_summaries() is True

    records = _records(home)
    assert _digest("Atlas notes are kept here.") in {row["sha256"] for row in records}
    assert get_topic_summary("atlas") is None


def test_replacing_an_entry_does_not_forget_the_old_text(store, home):
    store.add("memory", "The owner lives in Berlin")

    _run(store, action="replace", target="memory", old_text="Berlin", content="The owner lives in Munich")

    assert not _file(home, "forgotten.json").exists()


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


# ── owner re-add and automatic writers ───────────────────────────────────────

def test_background_add_of_forgotten_text_is_skipped_and_owner_add_is_accepted(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)

    skipped = _background(lambda: _run(store, action="add", target="memory", content=phrase))

    assert skipped["success"] is False and skipped["skipped"] is True
    assert "forgot" in skipped["reason"]
    assert store._entries_for("memory") == []
    assert _digest(phrase) in {row["sha256"] for row in _records(home)}

    accepted = _run(store, action="add", target="memory", content=phrase, owner_initiated=True)

    assert accepted["success"] is True
    assert accepted["forgotten_cleared"] == 1
    assert store._entries_for("memory") == [phrase]
    assert _digest(phrase) not in {row["sha256"] for row in _records(home)}


def test_foreground_model_add_of_forgotten_text_is_skipped_too(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)

    result = _run(store, action="add", target="memory", content=phrase)

    assert result["skipped"] is True
    assert store._entries_for("memory") == []


def test_owner_add_clears_only_the_records_the_new_text_contains(store, home):
    store.add("memory", "The owner drinks green tea")
    store.add("memory", "The owner wakes late")
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)
    memory_tool(action="remove", target="memory", old_text="wakes late", store=store)

    _run(store, action="add", target="memory", content="The owner drinks green tea at noon", owner_initiated=True)

    digests = {row["sha256"] for row in _records(home)}
    assert _digest("The owner drinks green tea") not in digests
    assert _digest("The owner wakes late") in digests


def test_staged_approval_replay_of_a_forgotten_add_is_skipped(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)

    result = apply_memory_pending({"action": "add", "target": "memory", "content": phrase}, store)

    assert result["skipped"] is True
    assert store._entries_for("memory") == []


def test_batch_with_a_forgotten_add_is_refused_as_a_whole(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)

    result = _run(store, operations=[{"action": "add", "content": phrase},
                                     {"action": "add", "content": "Other fact"}], target="memory")

    assert result["skipped"] is True
    assert store._entries_for("memory") == []


def test_replace_with_forgotten_text_is_refused_for_automatic_writers_and_accepted_for_the_owner(store, home):
    phrase = "The owner drinks green tea"
    store.add("memory", phrase)
    memory_tool(action="remove", target="memory", old_text="green tea", store=store)
    store.add("memory", "The owner drinks coffee")

    refused = _run(store, action="replace", target="memory", old_text="coffee", content=phrase)
    assert refused["skipped"] is True
    assert store._entries_for("memory") == ["The owner drinks coffee"]

    accepted = store.replace("memory", "coffee", phrase, owner_initiated=True)
    assert accepted["success"] is True
    assert store._entries_for("memory") == [phrase]


def test_topic_upsert_refuses_automatic_writers_and_accepts_the_owner(home):
    phrase = "The owner's favourite editor is Vim"
    upsert_topic_summary("editor-notes", f"Editor setup notes. {phrase}.", title="Editor notes")
    assert record_forgotten([phrase], source="memory") is None

    refused = upsert_topic_summary("editor-notes", f"Updated notes. {phrase}.", title="Editor notes")
    assert refused["skipped"] is True
    assert get_topic_summary("editor-notes")["summary"] == f"Editor setup notes. {phrase}."

    accepted = upsert_topic_summary("editor-notes", f"Updated notes. {phrase}.", title="Editor notes",
                                    owner_initiated=True)
    assert accepted["success"] is True
    assert accepted["forgotten_cleared"] == 1
    assert _digest(phrase) not in {row["sha256"] for row in _records(home)}


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


def test_a_paraphrase_in_an_old_chat_is_not_filtered(store, home, tmp_path):
    phrase = "The owner's favourite editor is Vim"
    store.add("memory", phrase)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s_paraphrase", source="cli")
    db.append_message("s_paraphrase", role="user", content="My favourite tea is sencha, the editor is fine.")
    _run(store, action="remove", target="memory", old_text=phrase)

    ids = {r["session_id"] for r in json.loads(session_search(query="favourite", db=db))["results"]}

    assert "s_paraphrase" in ids


def test_session_search_by_session_id_still_shows_the_transcript(store, home, tmp_path):
    phrase = "The owner's codename is Atlas"
    store.add("memory", phrase)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s_forgotten", source="cli")
    db.append_message("s_forgotten", role="user", content=f"Reminder: {phrase}.")
    _run(store, action="remove", target="memory", old_text=phrase)

    result = session_search(session_id="s_forgotten", db=db)

    assert phrase in result


def test_forgotten_text_in_a_topic_file_is_dropped_from_topic_recall(home):
    phrase = "The owner's favourite editor is Vim"
    assert record_forgotten([phrase], source="memory") is None
    topic_memory_path().parent.mkdir(parents=True, exist_ok=True)
    topic_memory_path().write_text(json.dumps({"version": 1, "topics": [{
        "key": "editor-notes", "title": "Editor notes", "summary": f"Editor setup notes. {phrase}.",
        "keywords": ["Vim"], "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
    }]}), encoding="utf-8")

    assert recall_topic_summaries("Vim editor notes") == []
