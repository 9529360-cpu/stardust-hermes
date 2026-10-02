"""Tests for gateway/mirror.py — session mirroring."""

import json
from unittest.mock import MagicMock, patch

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB

import gateway.mirror as mirror_mod
from gateway.mirror import (
    mirror_to_session,
    _find_session_id,
)


def _setup_sessions(home, sessions_data):
    """Write a legacy sessions.json under a temporary Hermes home."""
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")


@pytest.fixture
def profile_homes(tmp_path, monkeypatch):
    """Use independent real state.db files, not the suite's pinned DB path."""
    import hermes_state

    root = tmp_path / "hermes"
    secondary = root / "profiles" / "secondary"
    secondary.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH)
    return root, secondary


class TestFindSessionId:
    def test_finds_matching_session(self, tmp_path):
        _setup_sessions(tmp_path, {
            "agent:main:telegram:dm": {
                "session_id": "sess_abc",
                "origin": {"platform": "telegram", "chat_id": "12345"},
                "updated_at": "2026-01-01T00:00:00",
            }
        })

        token = set_hermes_home_override(tmp_path)
        try:
            result = _find_session_id("telegram", "12345")
        finally:
            reset_hermes_home_override(token)

        assert result == "sess_abc"

    def test_returns_most_recent(self, tmp_path):
        _setup_sessions(tmp_path, {
            "old": {
                "session_id": "sess_old",
                "origin": {"platform": "telegram", "chat_id": "12345"},
                "updated_at": "2026-01-01T00:00:00",
            },
            "new": {
                "session_id": "sess_new",
                "origin": {"platform": "telegram", "chat_id": "12345"},
                "updated_at": "2026-02-01T00:00:00",
            },
        })

        token = set_hermes_home_override(tmp_path)
        try:
            result = _find_session_id("telegram", "12345")
        finally:
            reset_hermes_home_override(token)

        assert result == "sess_new"

    def test_thread_id_disambiguates_same_chat(self, tmp_path):
        _setup_sessions(tmp_path, {
            "topic_a": {
                "session_id": "sess_topic_a",
                "origin": {"platform": "telegram", "chat_id": "-1001", "thread_id": "10"},
                "updated_at": "2026-01-01T00:00:00",
            },
            "topic_b": {
                "session_id": "sess_topic_b",
                "origin": {"platform": "telegram", "chat_id": "-1001", "thread_id": "11"},
                "updated_at": "2026-02-01T00:00:00",
            },
        })

        token = set_hermes_home_override(tmp_path)
        try:
            result = _find_session_id("telegram", "-1001", thread_id="10")
        finally:
            reset_hermes_home_override(token)

        assert result == "sess_topic_a"


class TestMirrorToSession:


    def test_successful_mirror_uses_user_id_for_group_session(self, tmp_path):
        _setup_sessions(tmp_path, {
            "alice": {
                "session_id": "sess_alice",
                "origin": {"platform": "telegram", "chat_id": "-1001", "user_id": "alice"},
                "updated_at": "2026-01-01T00:00:00",
            },
            "bob": {
                "session_id": "sess_bob",
                "origin": {"platform": "telegram", "chat_id": "-1001", "user_id": "bob"},
                "updated_at": "2026-02-01T00:00:00",
            },
        })

        token = set_hermes_home_override(tmp_path)
        try:
            with patch("gateway.mirror._append_to_sqlite") as mock_sqlite:
                result = mirror_to_session(
                    "telegram", "-1001", "Hello group!", source_label="cli", user_id="alice",
                )
        finally:
            reset_hermes_home_override(token)

        assert result is True
        mock_sqlite.assert_called_once()
        assert mock_sqlite.call_args[0][0] == "sess_alice"

    def test_no_matching_session(self, tmp_path):
        _setup_sessions(tmp_path, {})

        token = set_hermes_home_override(tmp_path)
        try:
            result = mirror_to_session("telegram", "99999", "Hello!")
        finally:
            reset_hermes_home_override(token)

        assert result is False


    def test_failed_sqlite_write_reports_false(self, tmp_path):
        """A mirror whose transcript write raises must not report success (#10130)."""
        _setup_sessions(tmp_path, {
            "dm": {
                "session_id": "sess_dm",
                "origin": {"platform": "telegram", "chat_id": "123"},
                "updated_at": "2026-01-01T00:00:00",
            },
        })
        broken_db = MagicMock()
        broken_db.find_session_by_origin.return_value = None  # resolve via sessions.json
        broken_db.append_message.side_effect = OSError("disk full")

        token = set_hermes_home_override(tmp_path)
        try:
            with patch("hermes_state_registry.acquire", return_value=broken_db), \
                 patch("hermes_state_registry.release_or_close"):
                result = mirror_to_session("telegram", "123", "Hello!")
        finally:
            reset_hermes_home_override(token)

        assert result is False
        broken_db.append_message.assert_called_once()


class TestProfileScopedLegacyFallback:
    def test_root_index_cannot_mirror_into_secondary_collision(self, profile_homes):
        """A restored shared raw ID must not turn another profile's row into the root target."""
        root, secondary = profile_homes
        _setup_sessions(root, {
            "root-chat": {
                "session_id": "shared-id",
                "origin": {"platform": "telegram", "chat_id": "root-chat"},
                "updated_at": "2026-01-01T00:00:00",
            },
        })
        # This row has the same raw ID but belongs to a different chat/profile.
        with SessionDB(db_path=secondary / "state.db") as db:
            db.create_session("shared-id", "telegram", session_key="secondary-chat", chat_id="secondary-chat")

        # Simulate a gateway imported under the default home before the profile
        # switch. On the pre-fix implementation this is the captured index;
        # create=True also lets the fixed implementation prove it ignores it.
        with patch.object(mirror_mod, "_SESSIONS_INDEX", root / "sessions" / "sessions.json", create=True):
            token = set_hermes_home_override(secondary)
            try:
                assert mirror_to_session("telegram", "root-chat", "private root reply") is False
            finally:
                reset_hermes_home_override(token)

        with SessionDB(db_path=secondary / "state.db") as db:
            assert db.get_messages("shared-id") == []

    def test_secondary_index_mirrors_only_its_own_legacy_session(self, profile_homes):
        root, secondary = profile_homes
        _setup_sessions(root, {
            "root-chat": {
                "session_id": "root-id",
                "origin": {"platform": "telegram", "chat_id": "shared-chat"},
                "updated_at": "2026-01-01T00:00:00",
            },
        })
        _setup_sessions(secondary, {
            "secondary-chat": {
                "session_id": "secondary-id",
                "origin": {"platform": "telegram", "chat_id": "shared-chat"},
                "updated_at": "2026-01-02T00:00:00",
            },
        })
        # Legacy session rows have no session_key, so DB-origin lookup misses.
        with SessionDB(db_path=secondary / "state.db") as db:
            db.create_session("secondary-id", "telegram")

        token = set_hermes_home_override(secondary)
        try:
            assert mirror_to_session("telegram", "shared-chat", "secondary reply") is True
        finally:
            reset_hermes_home_override(token)

        with SessionDB(db_path=secondary / "state.db") as db:
            assert [message["content"] for message in db.get_messages("secondary-id")] == ["secondary reply"]
        assert not (root / "state.db").exists()


class TestAppendToSqlite:
    def test_connection_is_released_after_use(self, tmp_path):
        """Verify _append_to_sqlite returns the shared SessionDB reference."""
        from gateway.mirror import _append_to_sqlite
        mock_db = MagicMock()
        released = []

        with patch("hermes_state_registry.acquire", return_value=mock_db), \
             patch(
                 "hermes_state_registry.release_or_close",
                 side_effect=lambda db: released.append(db),
             ):
            _append_to_sqlite("sess_1", {"role": "assistant", "content": "hello"})

        mock_db.append_message.assert_called_once()
        assert released == [mock_db], (
            "the shared handle must be released exactly once after use"
        )

