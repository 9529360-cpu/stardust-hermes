"""DL-P2.7 topic-memory provenance: stamps, recency-aware recall, flag-off parity.

Covers: source/observed_at/valid_until/verified persistence and validation,
expired-topic dropping, same-domain fork superseding (label + ranking),
old-format TOPICS.json compatibility, mid-write failure atomicity, profile
isolation of source paths, and flag-off byte-identity of recall output.
"""

from __future__ import annotations

import json

import pytest

from tools.topic_memory_store import (
    format_topic_recall,
    recall_topic_summaries,
    topic_memory_path,
    upsert_topic_summary,
)

@pytest.fixture
def topic_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("STARDUST_HOME", str(home))
    return home


def _write_phone_pair(stale_extra=None):
    """Current phone fact plus an older stale fork (the DL-P2.6 outdated shape)."""
    upsert_topic_summary(
        "phone", "阿豪现在用 iPhone 17。", title="手机", keywords=["手机", "设备"],
        observed_at="2026-10-20",
    )
    upsert_topic_summary(
        "phone-old", "阿豪的手机是 Pixel 8（旧记录）。", title="手机（旧）",
        keywords=["手机", "设备", "旧"], observed_at="2026-09-01", **(stale_extra or {}),
    )


# ── write path: stamps persist, invalid input is rejected ──────────────────


def test_provenance_stamps_persist_and_survive_partial_update(topic_home):
    upsert_topic_summary(
        "stardust", "v1", title="Stardust", keywords=["星尘"],
        source="session-123", observed_at="2026-10-01", verified=True,
    )
    upsert_topic_summary("stardust", "v2", title="Stardust", keywords=["星尘"])
    payload = json.loads(topic_memory_path().read_text(encoding="utf-8"))
    row = payload["topics"][0]
    assert row["summary"] == "v2"
    assert row["source"] == "session-123"
    assert row["observed_at"] == "2026-10-01"
    assert row["verified"] is True


def test_invalid_timestamps_and_order_are_rejected(topic_home):
    bad = upsert_topic_summary(
        "stardust", "v1", title="S", keywords=["s"], observed_at="not-a-date",
    )
    assert bad["success"] is False and "observed_at" in bad["error"]
    bad_order = upsert_topic_summary(
        "stardust", "v1", title="S", keywords=["s"],
        observed_at="2026-10-20", valid_until="2026-10-01",
    )
    assert bad_order["success"] is False and "valid_until" in bad_order["error"]
    if topic_memory_path().exists():
        assert "stardust" not in topic_memory_path().read_text(encoding="utf-8")


# ── recall: expired dropped, forks superseded, legacy untouched ────────────


def test_flag_on_drops_expired_and_ranks_current_first(topic_home):
    _write_phone_pair(stale_extra={"valid_until": "2026-10-01"})
    hits = recall_topic_summaries("阿豪现在用的是什么手机？", provenance=True)
    assert [row["key"] for row in hits] == ["phone"]


def test_flag_on_labels_superseded_fork_without_valid_until(topic_home):
    # No explicit expiry: the fork pair is still resolved by recency.
    _write_phone_pair()
    hits = recall_topic_summaries("阿豪现在用的是什么手机？", provenance=True)
    assert hits[0]["key"] == "phone"
    stale_rows = [row for row in hits if row["key"] == "phone-old"]
    if stale_rows:  # injected only when the limit/budget has room
        assert stale_rows[0]["superseded_on"] == "2026-10-20"
    rendered = format_topic_recall(hits)
    assert "older, superseded on 2026-10-20" in rendered
    assert "observed 2026-10-20" in rendered


def test_flag_off_keeps_both_forks_without_stamps(topic_home):
    _write_phone_pair(stale_extra={"valid_until": "2026-10-01"})
    hits = recall_topic_summaries("阿豪现在用的是什么手机？", provenance=False)
    keys = [row["key"] for row in hits]
    assert "phone-old" in keys  # legacy behaviour: both injected, no stamps
    assert all("superseded_on" not in row for row in hits)
    assert all("observed_at" not in row and "valid_until" not in row for row in hits)
    assert "superseded" not in format_topic_recall(hits)


def test_old_format_store_without_provenance_fields_recalls_unchanged(topic_home):
    path = topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "topics": [{
            "key": "phone", "title": "手机", "summary": "阿豪现在用 iPhone 17。",
            "keywords": ["手机"], "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )
    off = recall_topic_summaries("阿豪用什么手机？", provenance=False)
    on = recall_topic_summaries("阿豪用什么手机？", provenance=True)
    assert [row["key"] for row in off] == [row["key"] for row in on] == ["phone"]
    assert off[0]["summary"] == on[0]["summary"]


def test_supersede_detection_needs_at_least_two_shared_keywords(topic_home):
    # Two unrelated topics sharing one keyword must never supersede each other.
    upsert_topic_summary("a", "A current.", keywords=["共享"], observed_at="2026-10-20")
    upsert_topic_summary("b", "B older.", keywords=["共享"], observed_at="2026-09-01")
    hits = recall_topic_summaries("共享", provenance=True)
    assert all("superseded_on" not in row for row in hits)


# ── durability + isolation ─────────────────────────────────────────────────


def test_failed_write_never_corrupts_existing_file(topic_home, monkeypatch):
    upsert_topic_summary("stardust", "v1", title="Stardust", keywords=["星尘"],
                         source="s1", observed_at="2026-10-01")
    before = topic_memory_path().read_text(encoding="utf-8")

    # Simulate the atomic write failing mid-update (disk full). The existing
    # file must survive intact; the error propagates to the tool caller.
    from tools import topic_memory_store as tms

    def boom(*_args, **_kwargs):
        raise OSError("disk full mid-write")

    monkeypatch.setattr(tms, "_write_payload", boom)
    with pytest.raises(OSError):
        upsert_topic_summary("stardust", "v2", title="Stardust", keywords=["星尘"])
    assert topic_memory_path().read_text(encoding="utf-8") == before
    # And the store still recalls the pre-failure content afterwards.
    assert recall_topic_summaries("星尘", provenance=True)[0]["summary"] == "v1"


def test_source_never_leaks_across_profiles(tmp_path, monkeypatch):
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    monkeypatch.setenv("STARDUST_HOME", str(home_a))
    upsert_topic_summary("secret-topic", "Only in profile A.", keywords=["机密"],
                         source="D:/real/home/profile-a/session-1")
    monkeypatch.setenv("STARDUST_HOME", str(home_b))
    assert recall_topic_summaries("机密", provenance=True) == []
    upsert_topic_summary("other", "Profile B fact.", keywords=["普通"])
    text = (home_b / "memories" / "TOPICS.json").read_text(encoding="utf-8")
    assert "profile-a" not in text and "Only in profile A" not in text
