from __future__ import annotations

from types import SimpleNamespace

from agent import system_prompt
from hermes_cli import projects_db as pdb
from hermes_state import SessionDB


def test_project_facts_prompt_uses_project_authority_and_filters_unsafe_context(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd)

    with pdb.connect_closing() as conn:
        project_id = pdb.create_project(conn, name="Stardust", folders=[str(cwd)])
        pdb.add_project_fact(
            conn,
            project_id,
            "Python 3.12 is the supported runtime.",
            source_kind="repository",
            source_ref="pyproject.toml",
        )
        pdb.add_project_fact(
            conn,
            project_id,
            "Possibly prefers another runtime.",
            source_kind="inference",
            confidence=0.6,
        )
        pdb.add_project_fact(
            conn,
            project_id,
            "private deployment detail",
            source_kind="user",
            sensitive=True,
        )

    parts = system_prompt._project_fact_parts(
        SimpleNamespace(_context_cwd_is_launch_artifact=False)
    )

    assert len(parts) == 1
    block = parts[0]
    assert "Authority: projects.db" in block
    assert "Python 3.12 is the supported runtime." in block
    assert "pyproject.toml" not in block
    assert "Possibly prefers another runtime." not in block
    assert "private deployment detail" not in block


def test_verified_inference_can_enter_project_context(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd)

    with pdb.connect_closing() as conn:
        project_id = pdb.create_project(conn, name="Stardust", folders=[str(cwd)])
        fact_id = pdb.add_project_fact(
            conn,
            project_id,
            "Squash merge is the verified repository convention.",
            source_kind="inference",
            confidence=0.5,
        )
        assert pdb.verify_project_fact(conn, fact_id, project_id=project_id, verified_at=1234)

    block = system_prompt._project_fact_parts(
        SimpleNamespace(_context_cwd_is_launch_artifact=False)
    )[0]
    assert "[inference; confidence=1.00]" in block
    assert "Squash merge is the verified repository convention." in block


def test_launch_artifact_never_resolves_project_facts(monkeypatch):
    monkeypatch.setattr(
        system_prompt,
        "resolve_context_cwd",
        lambda: (_ for _ in ()).throw(AssertionError("should not resolve cwd")),
    )

    assert system_prompt._project_fact_parts(
        SimpleNamespace(_context_cwd_is_launch_artifact=True)
    ) == []

def test_project_fact_prompt_is_frozen_for_the_session(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd)

    with pdb.connect_closing() as conn:
        project_id = pdb.create_project(conn, name="Stardust", folders=[str(cwd)])
        pdb.add_project_fact(conn, project_id, "Initial fact.", source_kind="user")

    agent = SimpleNamespace(_context_cwd_is_launch_artifact=False)
    first = system_prompt._project_fact_parts(agent)
    assert "Initial fact." in first[0]

    with pdb.connect_closing() as conn:
        pdb.add_project_fact(conn, project_id, "Later fact.", source_kind="user")

    rebuilt = system_prompt._project_fact_parts(agent)
    assert rebuilt == first
    assert "Later fact." not in rebuilt[0]

    agent._frozen_project_fact_parts = None
    next_session = system_prompt._project_fact_parts(agent)
    assert "Later fact." in next_session[0]


def test_explicit_session_project_beats_cwd_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd_a = tmp_path / "repo-a"
    cwd_b = tmp_path / "repo-b"
    cwd_a.mkdir()
    cwd_b.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd_a)

    with pdb.connect_closing(home / "projects.db") as conn:
        project_a = pdb.create_project(conn, name="Alpha", folders=[str(cwd_a)])
        project_b = pdb.create_project(conn, name="Beta", folders=[str(cwd_b)])
        pdb.add_project_fact(conn, project_a, "Alpha fact.", source_kind="user")
        pdb.add_project_fact(conn, project_b, "Beta fact.", source_kind="user")

    session_db = SessionDB(db_path=home / "state.db")
    try:
        session_db.create_session(
            "session-1",
            source="desktop",
            cwd=str(cwd_a),
            project_id=project_b,
        )
        agent = SimpleNamespace(
            _context_cwd_is_launch_artifact=False,
            _session_db=session_db,
            session_id="session-1",
        )

        block = system_prompt._project_fact_parts(agent)[0]

        assert "Project: Beta" in block
        assert "Beta fact." in block
        assert "Alpha fact." not in block
    finally:
        session_db.close()


def test_missing_explicit_session_project_does_not_fall_back_to_cwd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd)

    with pdb.connect_closing(home / "projects.db") as conn:
        cwd_project = pdb.create_project(conn, name="CwdProject", folders=[str(cwd)])
        pdb.add_project_fact(conn, cwd_project, "Cwd-only fact.", source_kind="user")

    session_db = SessionDB(db_path=home / "state.db")
    try:
        session_db.create_session(
            "session-1",
            source="desktop",
            cwd=str(cwd),
            project_id="p_missing",
        )
        agent = SimpleNamespace(
            _context_cwd_is_launch_artifact=False,
            _session_db=session_db,
            session_id="session-1",
        )

        assert system_prompt._project_fact_parts(agent) == []
    finally:
        session_db.close()


def test_archived_explicit_project_does_not_fall_back_to_cwd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(system_prompt, "resolve_context_cwd", lambda: cwd)

    with pdb.connect_closing(home / "projects.db") as conn:
        project_id = pdb.create_project(conn, name="Archived", folders=[str(cwd)])
        pdb.add_project_fact(conn, project_id, "Archived fact.", source_kind="user")
        pdb.archive_project(conn, project_id)

    session_db = SessionDB(db_path=home / "state.db")
    try:
        session_db.create_session(
            "session-archived",
            source="desktop",
            cwd=str(cwd),
            project_id=project_id,
        )
        agent = SimpleNamespace(
            _context_cwd_is_launch_artifact=False,
            _session_db=session_db,
            session_id="session-archived",
        )
        assert system_prompt._project_fact_parts(agent) == []
    finally:
        session_db.close()
