from __future__ import annotations

import json

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import projects_db as pdb
from tools import project_tools


@pytest.fixture
def project_home(tmp_path):
    token = set_hermes_home_override(tmp_path)
    try:
        yield tmp_path
    finally:
        project_tools.set_project_context_callback(None)
        project_tools.set_project_workspace_callback(None)
        reset_hermes_home_override(token)


def _call(args: dict, task_id: str = "session-1") -> dict:
    return json.loads(project_tools._handle_project(args, task_id=task_id))


def test_agent_project_fact_lifecycle_is_scoped_to_current_session_project(project_home):
    with pdb.connect_closing() as conn:
        current = pdb.create_project(conn, name="Current")
        other = pdb.create_project(conn, name="Other")
        other_fact = pdb.add_project_fact(conn, other, "Other secret.", source_kind="user")

    project_tools.set_project_context_callback(lambda _task_id: current)

    added = _call({
        "action": "fact_add",
        "content": "Use Python 3.12.",
        "source_kind": "repository",
        "source_ref": "pyproject.toml",
    })
    assert added["success"] is True

    listed = _call({"action": "fact_list"})
    assert [fact["content"] for fact in listed["facts"]] == ["Use Python 3.12."]

    denied = _call({"action": "fact_supersede", "fact_id": other_fact})
    assert denied["success"] is False

    retired = _call({"action": "fact_supersede", "fact_id": added["fact_id"]})
    assert retired["success"] is True
    assert _call({"action": "fact_list"})["facts"] == []


def test_agent_cannot_promote_its_own_inference_to_certainty(project_home):
    with pdb.connect_closing() as conn:
        project_id = pdb.create_project(conn, name="Current")

    project_tools.set_project_context_callback(lambda _task_id: project_id)

    result = _call({
        "action": "fact_add",
        "content": "Probably prefers squash merge.",
        "source_kind": "inference",
        "confidence": 1.0,
    })
    assert result["success"] is False
    assert "confidence below 1.0" in result["error"]

    entry = project_tools.registry.get_entry("desktop_project")
    assert entry is not None
    actions = entry.schema["parameters"]["properties"]["action"]["enum"]
    assert "fact_verify" not in actions


def test_agent_fact_actions_require_session_project(project_home):
    project_tools.set_project_context_callback(lambda _task_id: None)
    result = _call({"action": "fact_list"})
    assert result == {"success": False, "error": "This session is not attached to a Project."}
