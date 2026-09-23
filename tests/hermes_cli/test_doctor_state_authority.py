from __future__ import annotations

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import doctor_state
from hermes_cli import projects_db as pdb


def test_state_authority_doctor_reports_owners_and_cwd_project(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    token = set_hermes_home_override(home)
    try:
        with pdb.connect_closing() as conn:
            project_id = pdb.create_project(
                conn,
                name="Stardust",
                folders=[str(workspace)],
            )
        monkeypatch.chdir(workspace)

        finding = doctor_state._check_state_authority(False)

        assert finding.issues == []
        out = capsys.readouterr().out
        assert "user_profile: memories/USER.md" in out
        assert "global_memory: memories/MEMORY.md" in out
        assert "project: projects.db" in out
        assert "session: state.db" in out
        assert "session_plan: state.db (sessions.model_config._todo_state)" in out
        assert "durable_task: kanban.db" in out
        assert "inference: owning domain with provenance" in out
        assert f"cwd project: Stardust ({project_id}) via folder ownership" in out
    finally:
        reset_hermes_home_override(token)
