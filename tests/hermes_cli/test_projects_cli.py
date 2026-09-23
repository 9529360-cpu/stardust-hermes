"""Tests for the `hermes project` CLI dispatch (hermes_cli/projects_cmd)."""

from __future__ import annotations

import argparse

import pytest

from hermes_cli import projects_cmd
from hermes_cli import projects_db as pdb


def _run(argv):
    """Build the project subparser, parse argv, and dispatch. Returns rc."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = projects_cmd.build_parser(sub)
    p.set_defaults(func=projects_cmd.projects_command)
    args = parser.parse_args(["project", *argv])
    return projects_cmd.projects_command(args)


def test_create_list_show(capsys, tmp_path):
    assert _run(["create", "My App", str(tmp_path), "--use"]) == 0
    out = capsys.readouterr().out
    assert "Created project" in out

    with pdb.connect_closing() as conn:
        projects = pdb.list_projects(conn)
        assert len(projects) == 1
        assert projects[0].name == "My App"
        # --use set it active.
        assert pdb.get_active_id(conn) == projects[0].id

    assert _run(["list"]) == 0
    assert "my-app" in capsys.readouterr().out

    assert _run(["show", "my-app"]) == 0
    assert "My App" in capsys.readouterr().out




def test_rename_and_archive(tmp_path):
    _run(["create", "Old Name", str(tmp_path)])
    assert _run(["rename", "old-name", "New Name"]) == 0
    with pdb.connect_closing() as conn:
        assert pdb.get_project(conn, "old-name").name == "New Name"

    assert _run(["archive", "old-name"]) == 0
    with pdb.connect_closing() as conn:
        assert pdb.list_projects(conn) == []
        assert len(pdb.list_projects(conn, include_archived=True)) == 1

    assert _run(["restore", "old-name"]) == 0
    with pdb.connect_closing() as conn:
        assert len(pdb.list_projects(conn)) == 1






def test_project_facts_cli_lifecycle(capsys, tmp_path):
    assert _run(["create", "Fact App", str(tmp_path)]) == 0
    capsys.readouterr()

    assert _run([
        "facts", "fact-app", "add", "Python 3.12 is required.",
        "--source", "repository", "--source-ref", "pyproject.toml",
    ]) == 0
    added = capsys.readouterr().out
    assert "Added project fact" in added

    with pdb.connect_closing() as conn:
        project = pdb.get_project(conn, "fact-app")
        facts = pdb.list_project_facts(conn, project.id)
        assert len(facts) == 1
        fact_id = facts[0].id
        assert facts[0].source_kind == "repository"
        assert facts[0].source_ref == "pyproject.toml"

    assert _run(["facts", "fact-app", "list"]) == 0
    listed = capsys.readouterr().out
    assert fact_id in listed
    assert "Python 3.12 is required." in listed

    assert _run(["facts", "fact-app", "verify", fact_id]) == 0
    assert "Verified project fact" in capsys.readouterr().out

    assert _run(["facts", "fact-app", "supersede", fact_id]) == 0
    assert "Superseded project fact" in capsys.readouterr().out

    assert _run(["facts", "fact-app", "list"]) == 0
    assert "No project facts." in capsys.readouterr().out
    assert _run(["facts", "fact-app", "list", "--all"]) == 0
    assert fact_id in capsys.readouterr().out


def test_project_facts_cli_rejects_certain_unverified_inference(capsys, tmp_path):
    assert _run(["create", "Fact App", str(tmp_path)]) == 0
    capsys.readouterr()

    assert _run([
        "facts", "fact-app", "add", "Probably uses squash merges.",
        "--source", "inference", "--confidence", "1.0",
    ]) == 2
    assert "confidence below 1.0" in capsys.readouterr().err
