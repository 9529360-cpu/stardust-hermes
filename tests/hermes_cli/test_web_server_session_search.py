import asyncio

from hermes_cli import web_server
import hermes_cli.web_routers.sessions as _rt_sessions
from hermes_state import SessionDB


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False
    opened_read_only = None
    requested_fields = None

    def __init__(self, *args, **kwargs):
        type(self).opened_read_only = kwargs.get("read_only")

    @staticmethod
    def _source_allowed(row, source=None, sources=None, exclude_sources=None):
        row_source = row.get("source")
        if source and row_source != source:
            return False
        if sources and row_source not in sources:
            return False
        if exclude_sources and row_source in exclude_sources:
            return False
        return True

    def search_sessions_by_id(
        self,
        query,
        limit=20,
        include_archived=True,
        source=None,
        sources=None,
        exclude_sources=None,
    ):
        assert query == "20260603"
        assert include_archived is True
        rows = [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]
        return [
            row
            for row in rows
            if self._source_allowed(
                row, source=source, sources=sources, exclude_sources=exclude_sources
            )
        ][:limit]

    def search_messages(
        self,
        query,
        source_filter=None,
        exclude_sources=None,
        limit=20,
        fields=None,
    ):
        assert query == "20260603*"
        type(self).requested_fields = fields
        rows = [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
        return [
            row
            for row in rows
            if self._source_allowed(
                row, sources=source_filter, exclude_sources=exclude_sources
            )
        ][:limit]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        return {"id": session_id, "parent_session_id": None}

    def get_compression_tip(self, session_id):
        return session_id

    def get_compression_chain(self, session_id):
        return [session_id]

    def list_sessions_rich(self, **kwargs):
        assert kwargs["order_by_last_active"] is True
        return []

    def close(self):
        self.closed = True


def _run_search(db, monkeypatch, **params):
    monkeypatch.setattr(
        _rt_sessions, "_open_session_db_for_profile",
        lambda _profile, **kwargs: SessionDB(db_path=db.db_path, **kwargs))
    monkeypatch.setattr(_rt_sessions, "_cron_default_profile", lambda: "owner-profile")
    monkeypatch.setattr(_rt_sessions, "_cron_profile_home", lambda profile: (profile, db.db_path.parent))
    return asyncio.run(_rt_sessions.search_sessions(**params))["results"]


def test_search_includes_archived_title_matches_and_dedupes_metadata_and_fts(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        # Neither the archived session's ID nor its body contains the query.
        db.create_session("old-history", source="cli", model="m")
        db.set_session_title("old-history", "Needle archive")
        db.append_message("old-history", "user", "ordinary past conversation")
        db.set_session_archived("old-history", True)
        db.create_session("compression-root", source="cli", model="m")
        db.set_session_title("compression-root", "Needle previous title")
        db.append_message("compression-root", "user", "needle in the original segment")
        db.end_session("compression-root", end_reason="compression")
        db.create_session("stale-sibling", source="cli", model="m", parent_session_id="compression-root")
        db.set_session_title("stale-sibling", "Obsolete run")
        db.end_session("stale-sibling", end_reason="ws_orphan_reap")
        db.create_session("live-tip", source="cli", model="m", parent_session_id="compression-root")
        db.append_message("live-tip", "user", "needle contentonly in message")
        # Model the publish window: child exists before parent ended_at and
        # has not inherited its title yet, while its preview is already present.
        root_ended_at = db.get_session("compression-root")["ended_at"]
        db._write_sql(
            "UPDATE sessions SET title = NULL, started_at = ? WHERE id = ?",
            (root_ended_at - 1, "live-tip"))
        db._write_sql(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (root_ended_at - 2, "stale-sibling"))
        assert db.get_compression_tip("compression-root") == "live-tip"
        db.create_session(
            "separate-branch", source="cli", model="m", parent_session_id="compression-root",
            model_config={"_branched_from": "compression-root"})
        db.set_session_title("separate-branch", "Needle branch")
        db.append_message("separate-branch", "user", "needle branch body")
        for index in range(45):
            db.create_session(f"recent-{index}", source="cli", model="m")

        rows = _run_search(db, monkeypatch, q="needle", limit=10)

        assert {row["session_id"] for row in rows} == {"old-history", "live-tip", "separate-branch"}
        assert sum(row["session_id"] == "live-tip" for row in rows) == 1
        tip = next(row for row in rows if row["session_id"] == "live-tip")
        assert tip["lineage_root"] == "compression-root"
        assert tip["title"] == "Needle previous title"
        assert tip["preview"]
        assert next(row for row in rows if row["session_id"] == "separate-branch")["lineage_root"] == "separate-branch"
        for query in ("compression-root", "contentonly"):
            found = _run_search(db, monkeypatch, q=query, limit=10)
            assert len(found) == 1
            assert found[0]["session_id"] == "live-tip"
            assert found[0]["lineage_root"] == "compression-root"
            assert found[0]["title"] == "Needle previous title"
        archived = next(row for row in rows if row["session_id"] == "old-history")
        assert archived["title"] == "Needle archive"
        assert archived["archived"] is True
        assert archived["profile"] == "owner-profile"
    finally:
        db.close()


def test_search_metadata_respects_source_filters_limit_and_id_priority(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("needle-id-priority", source="cli", model="m")
        db.set_session_title("needle-id-priority", "needle metadata")
        db.create_session("telegram-history", source="telegram", model="m")
        db.set_session_title("telegram-history", "needle telegram metadata")
        db.create_session("desktop-history", source="desktop", model="m")
        db.set_session_title("desktop-history", "needle desktop metadata")

        rows = _run_search(
            db, monkeypatch, q="needle", limit=1,
            sources="cli,desktop", exclude_sources="desktop")
        assert [row["session_id"] for row in rows] == ["needle-id-priority"]
        assert rows[0]["snippet"] == "Session ID: needle-id-priority"
        assert rows[0]["profile"] == "owner-profile"

        metadata_rows = _run_search(
            db, monkeypatch, q="metadata", limit=10,
            sources="cli,desktop", exclude_sources="desktop", profile="personal")
        assert [row["session_id"] for row in metadata_rows] == ["needle-id-priority"]
        assert metadata_rows[0]["profile"] == "personal"
        source_rows = _run_search(db, monkeypatch, q="metadata", source="telegram", limit=1)
        assert [row["session_id"] for row in source_rows] == ["telegram-history"]
    finally:
        db.close()


def test_search_cjk_body_matches_without_title_or_id_help(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("past-trip", source="desktop", model="m")
        db.set_session_title("past-trip", "周末安排记录")
        db.append_message("past-trip", "user", "先保留这份讨论记录。")
        db.append_message("past-trip", "assistant", "之前讨论的海风灯塔路线已经确认。")

        rows = _run_search(db, monkeypatch, q="海风灯塔", profile="personal")

        assert [row["session_id"] for row in rows] == ["past-trip"]
        assert "海风灯塔" in rows[0]["snippet"].replace(">>>", "").replace("<<<", "")
        assert rows[0]["title"] == "周末安排记录"
        assert rows[0]["profile"] == "personal"
    finally:
        db.close()


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    _FakeSessionDB.opened_read_only = None
    _FakeSessionDB.requested_fields = None
    monkeypatch.setattr("hermes_state.SessionDB", _FakeSessionDB)
    monkeypatch.setattr(_rt_sessions, "_cron_default_profile", lambda: "default")

    response = asyncio.run(_rt_sessions.search_sessions(q="20260603", limit=2))

    assert _FakeSessionDB.requested_fields is not None
    assert "context" not in _FakeSessionDB.requested_fields
    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "id": "20260603_090200_exact",
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
                "profile": "default",
            },
            {
                "id": "content_session",
                "session_id": "content_session",
                "lineage_root": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
                "profile": "default",
            },
        ]
    }
    assert _FakeSessionDB.opened_read_only is True
