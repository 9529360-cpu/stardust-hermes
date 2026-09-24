from __future__ import annotations

from hermes_state import SessionDB


def test_project_id_inherits_across_session_lineage_and_can_be_rebound(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("parent", source="desktop", project_id="p_alpha")
        db.create_session("child", source="desktop", parent_session_id="parent")

        assert db.get_session("child")["project_id"] == "p_alpha"

        assert db.set_session_project("child", "p_beta") is True
        assert db.get_session("child")["project_id"] == "p_beta"

        assert db.set_session_project("child", None) is True
        assert db.get_session("child")["project_id"] is None
    finally:
        db.close()


def test_session_import_drops_profile_local_project_owner(tmp_path):
    db = SessionDB(db_path=tmp_path / "import.db")
    try:
        result = db.import_sessions([
            {
                "id": "imported",
                "source": "desktop",
                "cwd": "/portable/repo",
                "project_id": "p_from_another_profile",
                "messages": [],
            }
        ])

        assert result["ok"] is True
        row = db.get_session("imported")
        assert row["cwd"] == "/portable/repo"
        assert row["project_id"] is None
    finally:
        db.close()
