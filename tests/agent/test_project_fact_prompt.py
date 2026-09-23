from __future__ import annotations

from types import SimpleNamespace

from agent import system_prompt
from hermes_cli import projects_db as pdb


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
        assert pdb.verify_project_fact(conn, fact_id, verified_at=1234)

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
