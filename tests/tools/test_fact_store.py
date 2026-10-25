"""Regression tests for the bi-temporal fact store (DL-P2.9)."""
from __future__ import annotations

import json

import pytest

from tools import fact_store as fs


@pytest.fixture()
def store_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(home))
    yield home


def _add(store_home, subject="residence", content="Ah Hao lives in Milan.", **kw):
    return fs.add_fact(subject, kw.pop("predicate", "state"), content, **kw)


def test_add_then_recall_returns_only_current(store_home):
    r1 = _add(store_home, content="Ah Hao lives in Kuala Lumpur.")
    r2 = fs.update_fact(r1["target_id"], content="Ah Hao lives in Milan.")
    assert r2["action"] == "UPDATE"
    recalled = fs.recall_facts("Where does Ah Hao live?")
    contents = [row["content"] for row in recalled]
    assert contents == ["Ah Hao lives in Milan."]
    history = fs.fact_history("residence")
    assert len(history) == 2  # closed + open: nothing physically deleted
    closed = next(h for h in history if h["valid_to"])
    assert closed["superseded_by"] == r2["target_id"]


def test_duplicate_same_fact_is_noop(store_home):
    _add(store_home)
    again = _add(store_home)
    assert again["action"] == "NOOP"
    assert len(fs.load_facts(include_history=True)) == 1


def test_retraction_closes_without_delete(store_home):
    r1 = _add(store_home, subject="fav-food", content="Ah Hao loves Sichuan hotpot.")
    res = fs.close_fact(r1["target_id"], reason="retracted")
    assert res["action"] == "CLOSE"
    assert fs.load_facts() == []  # not valid any more
    assert len(fs.load_facts(include_history=True)) == 1  # still on disk


def test_old_format_records_load_unchanged(store_home):
    # Pre-DL-P2.9 shape: no valid_from/valid_to fields.
    legacy = {
        "version": 1,
        "facts": [
            {"id": "legacy-1", "subject": "job", "predicate": "state",
             "content": "Ah Hao works at a bank.", "confidence": 0.8}
        ],
    }
    path = fs.fact_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    rows = fs.load_facts()
    assert len(rows) == 1 and rows[0]["content"] == "Ah Hao works at a bank."
    # And a normal update on top of a legacy record keeps the history row.
    res = fs.update_fact("legacy-1", content="Ah Hao is an indie developer.")
    assert res["success"] is True
    assert len(fs.load_facts(include_history=True)) == 2


def test_empty_store_is_safe(store_home):
    assert fs.load_facts() == []
    assert fs.recall_facts("anything") == []
    assert fs.close_fact("missing")["success"] is False


def test_flag_default_off(store_home, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    # Lazy read mirrors topic_provenance_enabled/hybrid_recall: no config -> OFF.
    assert fs.fact_pipeline_enabled() is False


def test_flag_on_via_config(store_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})
    assert fs.fact_pipeline_enabled() is True


def test_flag_off_via_config(store_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": False})
    assert fs.fact_pipeline_enabled() is False


def home_cfg(store_home, memory_yaml: str) -> dict:
    import yaml

    return {"memory": yaml.safe_load(f"memory:\n  {memory_yaml}\n")["memory"]}


def test_corrupt_store_loads_empty_and_write_recovers(store_home):
    path = fs.fact_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert fs.load_facts() == []  # logged skip, no crash
    res = _add(store_home, subject="os", content="Ah Hao uses Windows 11.")
    assert res["success"] is True


def test_write_is_atomic_and_locked(store_home):
    # Two rapid writers must both land; the .lock file discipline is inherited
    # from MemoryStore._file_lock (same as TOPICS.json).
    for i in range(5):
        assert _add(store_home, subject=f"subj-{i}", content=f"Fact number {i} about testing.")["success"]
    assert len(fs.load_facts()) == 5


def test_subject_normalisation(store_home):
    res = _add(store_home, subject="Preferred  MODEL!", content="Ah Hao prefers gpt-6-luna.")
    rows = fs.load_facts()
    assert rows[0]["subject"] == "preferred-model"
    assert res["success"] is True


def test_max_facts_guard(store_home, monkeypatch):
    monkeypatch.setattr(fs, "_MAX_FACTS", 3)
    for i in range(3):
        assert _add(store_home, subject=f"s{i}", content=f"fact {i}")["success"] is True
    res = _add(store_home, subject="s9", content="fact 9")
    assert res["success"] is False and "already has" in res["error"]


def test_update_closed_fact_rejected(store_home):
    r1 = _add(store_home, content="Ah Hao works at a bank.")
    fs.close_fact(r1["target_id"], reason="retracted")
    res = fs.update_fact(r1["target_id"], content="Ah Hao is an indie developer.")
    assert res["success"] is False and "already closed" in res["error"]
