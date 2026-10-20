"""Profile-owned Task Center RPC: real contract dispatch, isolation, and read-only views."""

from __future__ import annotations

import json
import sqlite3
import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def server(tmp_path):
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value=tmp_path / "hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")
    return mod


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    root = tmp_path / "hermes"
    monkeypatch.setattr("hermes_constants._get_platform_default_hermes_home", lambda: root)
    token = set_hermes_home_override(root)
    yield root
    reset_hermes_home_override(token)


def _dispatch(server, params=None):
    return server.handle_request({
        "jsonrpc": "2.0",
        "id": "tasks-test",
        "method": "tasks.list",
        "params": params or {},
    })


def _seed(home):
    from cron.jobs import create_job
    from cron.executions import create_execution
    from hermes_cli.goals import GoalState, save_goal

    job = create_job(
        name="Morning digest",
        schedule="every 1h",
        prompt="Summarize the inbox",
    )
    execution = create_execution(job["id"], source="scheduler")
    save_goal("session-1", GoalState(goal="Ship the migration", turns_used=2, max_turns=20))
    return execution


def test_real_dispatch_runs_tasks_list_on_transport_pool(server, home):
    from cron.jobs import create_job

    class CaptureTransport:
        def __init__(self):
            self.frames = []
            self.ready = threading.Event()

        def write(self, frame):
            self.frames.append(frame)
            self.ready.set()
            return True

        def close(self):
            return None

    job = create_job(name="Dispatch task", schedule="every 1h", prompt="Read only")
    transport = CaptureTransport()
    request = {
        "jsonrpc": "2.0",
        "id": "tasks-dispatch",
        "method": "tasks.list",
        "params": {},
    }

    result = server.dispatch(request, transport=transport)

    assert result is None  # Long handlers respond through the bound transport.
    assert transport.ready.wait(15)
    assert len(transport.frames) == 1
    frame = transport.frames[0]
    assert frame["id"] == "tasks-dispatch"
    assert "error" not in frame, frame
    assert any(row["task_id"] == f"cron:{job['id']}" for row in frame["result"]["tasks"])


def test_method_is_registered_as_long_handler_and_contract(server):
    from tui_gateway.contracts import registry

    assert "tasks.list" in server._methods
    assert "tasks.list" in server._LONG_HANDLERS
    assert "tasks.list" in registry.METHODS


def test_dispatch_returns_allowlisted_cron_and_goal_rows_without_writes(server, home):
    execution = _seed(home)
    from hermes_cli.goals import GoalState, save_goal

    save_goal("session-done", GoalState(goal="Completed goal", status="done", last_turn_at=1_800_000_000))
    save_goal("session-cleared", GoalState(goal="Cleared goal", status="cleared"))
    before_files = {path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    before_dirs = {path.relative_to(home) for path in home.rglob("*") if path.is_dir()}

    reply = _dispatch(server)

    after_files = {path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    after_dirs = {path.relative_to(home) for path in home.rglob("*") if path.is_dir()}
    assert after_dirs == before_dirs
    volatile_sidecars = {"-shm", "-wal"}
    stable_paths = {
        path for path in after_files
        if not any(path.name.endswith(suffix) for suffix in volatile_sidecars)
    }
    before_stable_paths = {
        path for path in before_files
        if not any(path.name.endswith(suffix) for suffix in volatile_sidecars)
    }
    assert stable_paths == before_stable_paths
    assert all(after_files[path] == before_files[path] for path in before_stable_paths)

    assert "error" not in reply, reply
    result = reply["result"]
    by_id = {row["task_id"]: row for row in result["tasks"]}
    assert result["scoped"] == ""
    job_id = execution["job_id"]
    assert by_id[f"cron:{job_id}"]["title"] == "Morning digest"
    assert by_id[f"cron-run:{execution['id']}"]["parent_task_id"] == f"cron:{job_id}"
    assert by_id["goal:session-1"]["progress_current"] == 2
    assert by_id["goal:session-1"]["durability"] == "session_bound"
    assert by_id["goal:session-done"]["state"] == "completed"
    assert by_id["goal:session-done"]["terminal"] is True
    assert by_id["goal:session-done"]["updated_at"] == 1_800_000_000
    assert "goal:session-cleared" not in by_id
    assert set(by_id[f"cron:{job_id}"]) == {
        "task_id", "title", "state", "rail", "durability", "background", "phase",
        "started_at", "updated_at", "next_run_at", "progress_current", "progress_total",
        "parent_session_id", "parent_task_id", "requires_approval", "recoverable",
        "recovery_action", "summary", "detail", "files", "needs_attention", "terminal", "profile",
    }


def test_dispatch_rejects_unknown_params_and_clamps_oversize_limit(server, home):
    invalid = _dispatch(server, {"unexpected": True})
    assert invalid["error"]["code"] == 4000

    for index in range(55):
        from cron.executions import create_execution

        create_execution(f"job-{index}", source="scheduler")
    result = _dispatch(server, {"limit": 50})["result"]
    assert len(result["tasks"]) <= 150


def test_profile_binding_reads_the_selected_profile_and_returns_canonical_scope(server, tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = profiles / "coder"
    profile.mkdir()
    monkeypatch.setattr(server, "_profile_home", lambda name: profile if name == "coder" else None)
    monkeypatch.setattr("hermes_constants.named_profile_is_deleted", lambda path: False)
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from cron.jobs import create_job

    token = set_hermes_home_override(profile)
    try:
        job = create_job(name="Profile task", schedule="every 1h", prompt="Do profile work")
        from cron.executions import create_execution

        create_execution(job["id"], source="scheduler")
    finally:
        reset_hermes_home_override(token)

    reply = _dispatch(server, {"profile": "coder"})

    assert "error" not in reply, reply
    assert reply["result"]["scoped"] == "coder"
    assert {row["title"] for row in reply["result"]["tasks"]} == {"Profile task"}
    assert {row["profile"] for row in reply["result"]["tasks"]} == {"coder"}
    assert get_hermes_home() != profile


def test_unknown_deleted_profile_and_bad_limit_fail_closed(server, tmp_path, monkeypatch):
    reply = _dispatch(server, {"limit": "many"})
    assert reply["error"]["code"] == 4000

    unknown = _dispatch(server, {"profile": "missing"})
    assert unknown["error"]["code"] == 4064

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = profiles / "deleted"
    profile.mkdir()
    monkeypatch.setattr(server, "_profile_home", lambda name: profile)
    monkeypatch.setattr("hermes_constants.named_profile_is_deleted", lambda path: True)
    deleted = _dispatch(server, {"profile": "deleted"})
    assert deleted["error"]["code"] == 4064


def test_row_text_is_redacted_and_single_line(server, home):
    from cron.jobs import create_job

    secret = "sk-proj-12345678901234567890abcdef"
    job = create_job(name=f"Leaked {secret}\nsecond line", schedule="every 1h", prompt="safe")

    reply = _dispatch(server)

    assert "error" not in reply, reply
    row = next(row for row in reply["result"]["tasks"] if row["task_id"] == f"cron:{job['id']}")
    assert secret not in row["title"]
    assert "\n" not in row["title"]


def test_goal_ids_with_unsafe_content_are_opaque_and_parent_links_are_omitted(server, home):
    from hermes_cli.goals import GoalState, save_goal

    unsafe = "session-1\n«redacted:sk-secret-token»"
    save_goal(unsafe, GoalState(goal="Safe goal"))
    reply = _dispatch(server)

    assert "error" not in reply, reply
    row = next(row for row in reply["result"]["tasks"] if row["title"] == "Safe goal")
    assert "session-1" not in row["task_id"]
    assert "sk-secret-token" not in json.dumps(row)
    assert row["parent_session_id"] is None


def test_identifier_gate_rejects_secret_shaped_values_but_keeps_valid_links():
    from tools.task_projection import _safe_identifier

    token = "sk-" + "Z" * 48  # Synthetic fixture; never an actual credential.
    assert _safe_identifier(token) is None
    assert _safe_identifier("goal:" + token, allow_opaque=True).startswith("opaque-sha256:")
    for owner in ("session-1", "3e8821af-1122-44cc-88bb-aabbccddeeff", "goal:session-1"):
        assert _safe_identifier(owner) == owner


def test_timed_wait_goal_is_queued_without_user_attention(server, home, monkeypatch):
    from hermes_cli.goals import GoalState, save_goal
    from hermes_cli.goals_view import list_goal_states

    save_goal("wait-session", GoalState(goal="Wait for child", status="active"))
    save_goal("paused-session", GoalState(goal="Paused goal", status="paused"))
    original = list_goal_states
    monkeypatch.setattr("hermes_cli.goals_view.list_goal_states", lambda limit=20: [
        {**row, "waiting": True} for row in original(limit=limit)
    ])

    reply = _dispatch(server)
    by_title = {row["title"]: row for row in reply["result"]["tasks"]}
    assert by_title["Wait for child"]["state"] == "queued"
    assert by_title["Wait for child"]["phase"] == "waiting"
    assert by_title["Wait for child"]["needs_attention"] is False
    assert by_title["Paused goal"]["state"] == "paused"

    from agent.assistant_intent import AssistantTaskState, project_goal

    projection = project_goal(
        "terminal-session", {"status": "done", "goal": "Terminal"}, waiting=True
    )
    assert projection.state is AssistantTaskState.COMPLETED


def test_rpc_does_not_bind_secret_or_terminal_scope(server, home, monkeypatch):
    from tui_gateway import model_switch

    def hydrate_forbidden(*args, **kwargs):
        raise AssertionError("tasks.list must not hydrate profile secrets")

    monkeypatch.setattr(model_switch, "_profile_runtime_scope_tokens", hydrate_forbidden)
    assert _dispatch(server)["result"]["tasks"] == []
