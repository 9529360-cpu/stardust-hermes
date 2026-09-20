import json
from contextlib import contextmanager
from types import SimpleNamespace

from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
from tools import assistant_tasks


def test_create_splits_independent_work_and_preserves_durable_controls(monkeypatch):
    calls = []

    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")

    def fake_create(args):
        calls.append(dict(args))
        if args["title"] == "Broken sibling":
            return json.dumps({"success": False, "error": "bad task"})
        return json.dumps(
            {
                "ok": True,
                "task_id": f"t_{len(calls)}",
                "status": "ready",
                "workspace_kind": args.get("workspace_kind") or "scratch",
                "workspace_path": args.get("workspace_path"),
                "project_id": args.get("project"),
                "subscribed": True,
            }
        )

    monkeypatch.setattr("tools.kanban_tools._handle_create", fake_create)

    result = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="create",
            session_id="session-7",
            request_id="call-42",
            tasks=[
                {
                    "title": "Convert report to PDF",
                    "instruction": "Convert the desktop report.docx to PDF and verify the PDF opens.",
                },
                {
                    "title": "Prepare Japan flight",
                    "instruction": "Find a suitable flight to Japan tomorrow and prepare the booking.",
                    "approval_required": True,
                },
                {
                    "title": "Continue Stardust",
                    "instruction": "Continue the Stardust project until the requested feature is complete.",
                    "continuous": True,
                    "project": "stardust",
                    "workspace_kind": "worktree",
                },
                {
                    "title": "Broken sibling",
                    "instruction": "This one fails without cancelling its siblings.",
                },
            ],
        )
    )

    assert result["summary"] == {"requested": 4, "created": 3, "failed": 1}
    assert [row["title"] for row in result["created"]] == [
        "Convert report to PDF",
        "Prepare Japan flight",
        "Continue Stardust",
    ]
    assert calls[0]["session_id"] == "session-7"
    assert calls[0]["assignee"] == "default"
    assert calls[0]["_assistant_owner_key"] == "local"
    assert calls[0]["idempotency_key"].endswith(":call-42:0")
    assert calls[1]["idempotency_key"].endswith(":call-42:1")
    assert calls[2]["idempotency_key"].endswith(":call-42:2")
    assert "local" not in calls[0]["idempotency_key"]
    assert calls[2]["goal_mode"] is True
    assert calls[2]["project"] == "stardust"
    assert calls[2]["workspace_kind"] == "worktree"

    assert "[User-control boundary]" not in calls[0]["body"]
    assert "[User-control boundary]" in calls[1]["body"]
    assert 'kanban_block(kind="needs_input"' in calls[1]["body"]
    assert "final external commit" in calls[1]["body"]


def test_create_retry_uses_same_idempotency_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "tools.kanban_tools._handle_create",
        lambda args: calls.append(dict(args))
        or json.dumps(
            {
                "ok": True,
                "task_id": "t_same",
                "status": "ready",
                "workspace_kind": "scratch",
                "project_id": None,
                "subscribed": True,
            }
        ),
    )

    payload = [{"title": "Long task", "instruction": "Keep working until done."}]
    for _ in range(2):
        assistant_tasks.assistant_tasks_tool(
            action="create",
            tasks=payload,
            session_id="s1",
            request_id="tool-call-stable",
        )

    assert [call["idempotency_key"] for call in calls] == [
        "assistant:local:tool-call-stable:0",
        "assistant:s1:tool-call-stable:0",
    ]


def test_list_is_read_only_projection_of_kanban_authority(monkeypatch):
    rows = [
        SimpleNamespace(
            id="t_active",
            title="Continue project",
            status="running",
            assignee="default",
            project_id="project-1",
            workspace_kind="worktree",
            workspace_path="/repo/.worktrees/t_active",
            created_at=100,
            started_at=110,
            completed_at=None,
            block_kind=None,
            last_failure_error=None,
            result=None,
        ),
        SimpleNamespace(
            id="t_wait",
            title="Book flight",
            status="blocked",
            assignee="default",
            project_id=None,
            workspace_kind="scratch",
            workspace_path=None,
            created_at=90,
            started_at=95,
            completed_at=None,
            block_kind="needs_input",
            last_failure_error=None,
            result=None,
        ),
        SimpleNamespace(
            id="t_done",
            title="Convert PDF",
            status="done",
            assignee="default",
            project_id=None,
            workspace_kind="scratch",
            workspace_path=None,
            created_at=80,
            started_at=81,
            completed_at=85,
            block_kind=None,
            last_failure_error=None,
            result="saved report.pdf",
        ),
    ]

    class FakeKb:
        @staticmethod
        def list_tasks(conn, **kwargs):
            assert kwargs["assistant_owner_key"] == "local"
            return rows

        @staticmethod
        def latest_run(conn, task_id):
            if task_id == "t_wait":
                return SimpleNamespace(summary="Waiting for approval of the exact itinerary and price.")
            return None

    @contextmanager
    def fake_board(_board):
        yield FakeKb, object()

    monkeypatch.setattr("tools.kanban_tools._board", fake_board)

    active = json.loads(assistant_tasks.assistant_tasks_tool(action="list"))
    assert [row["task_id"] for row in active["tasks"]] == ["t_active", "t_wait"]
    waiting = next(row for row in active["tasks"] if row["task_id"] == "t_wait")
    assert waiting["needs_attention"] is True
    assert waiting["block_kind"] == "needs_input"
    assert "approval" in waiting["detail"].lower()

    all_rows = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            include_completed=True,
            task_ids=["t_done"],
        )
    )
    assert [row["task_id"] for row in all_rows["tasks"]] == ["t_done"]


def test_inline_executor_binds_exact_session_and_tool_call(monkeypatch):
    captured = {}

    def fake_tool(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True})

    monkeypatch.setattr("tools.assistant_tasks.assistant_tasks_tool", fake_tool)
    agent = SimpleNamespace(session_id="runtime-session-9")
    ctx = InlineToolContext(effective_task_id="turn-task", tool_call_id="call-9")

    result = INLINE_TOOL_EXECUTORS["assistant_tasks"](
        agent,
        {"action": "create", "tasks": [{"title": "A", "instruction": "B"}]},
        ctx,
    )

    assert json.loads(result)["ok"] is True
    assert captured["session_id"] == "runtime-session-9"
    assert captured["request_id"] == "call-9"
    assert captured["action"] == "create"



def test_owner_key_is_local_across_desktop_conversations(monkeypatch):
    monkeypatch.setattr(
        "gateway.session_context.session_is_messaging_surface",
        lambda: False,
    )
    assert assistant_tasks._resolve_owner_key() == "local"


def test_messaging_owner_key_is_stable_user_not_chat(monkeypatch):
    values = {
        "HERMES_SESSION_PLATFORM": "telegram",
        "HERMES_SESSION_SOURCE": "telegram",
        "HERMES_SESSION_USER_ID_ALT": "",
        "HERMES_SESSION_USER_ID": "user-42",
    }
    monkeypatch.setattr(
        "gateway.session_context.session_is_messaging_surface",
        lambda: True,
    )
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": values.get(name, default),
    )

    assert assistant_tasks._resolve_owner_key() == "messaging:telegram:user-42"

    values["HERMES_SESSION_USER_ID"] = ""
    assert assistant_tasks._resolve_owner_key() is None


def test_local_owner_survives_origin_session_deletion(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")

    with SessionDB(db_path=home / "state.db") as db:
        db.create_session("chat-A", source="desktop")
        db.append_message("chat-A", "user", "start three jobs")

    created = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="create",
            owner_key="local",
            session_id="chat-A",
            request_id="call-A",
            tasks=[
                {
                    "title": "Convert report to PDF",
                    "instruction": "Convert report.docx to PDF and verify it opens.",
                },
                {
                    "title": "Prepare Japan flight",
                    "instruction": "Prepare tomorrow's Japan flight booking.",
                    "approval_required": True,
                },
                {
                    "title": "Continue Stardust",
                    "instruction": "Keep developing Stardust until this feature is complete.",
                    "continuous": True,
                },
            ],
        )
    )
    assert created["summary"]["created"] == 3

    with SessionDB(db_path=home / "state.db") as db:
        assert db.delete_session("chat-A")
        assert db.get_session("chat-A") is None
        db.create_session("chat-B", source="desktop")
        db.append_message("chat-B", "user", "what happened to my three jobs?")

    # B does not need A's transcript or id. The durable owner is the lookup key.
    recalled = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
        )
    )
    assert {task["title"] for task in recalled["tasks"]} == {
        "Convert report to PDF",
        "Prepare Japan flight",
        "Continue Stardust",
    }

    with kbc.connect() as conn:
        stored = kb.list_tasks(conn, assistant_owner_key="local", include_archived=True)
        assert len(stored) == 3
        assert {task.session_id for task in stored} == {"chat-A"}
        assert {task.assistant_owner_key for task in stored} == {"local"}


def test_task_listing_isolated_by_assistant_owner(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    with kbc.connect() as conn:
        kb.create_task(
            conn,
            title="Alice task",
            assignee="default",
            assistant_owner_key="messaging:telegram:alice",
        )
        kb.create_task(
            conn,
            title="Bob task",
            assignee="default",
            assistant_owner_key="messaging:telegram:bob",
        )

    alice = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="messaging:telegram:alice",
            include_completed=True,
        )
    )
    bob = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="messaging:telegram:bob",
            include_completed=True,
        )
    )
    assert [task["title"] for task in alice["tasks"]] == ["Alice task"]
    assert [task["title"] for task in bob["tasks"]] == ["Bob task"]



def test_owner_identity_is_not_embedded_in_idempotency_key(monkeypatch):
    calls = []
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "tools.kanban_tools._handle_create",
        lambda args: calls.append(dict(args))
        or json.dumps({
            "ok": True,
            "task_id": "t_private",
            "status": "ready",
            "workspace_kind": "scratch",
            "project_id": None,
            "subscribed": True,
        }),
    )

    owner = "messaging:telegram:user-secret-42"
    assistant_tasks.assistant_tasks_tool(
        action="create",
        owner_key=owner,
        session_id="chat-A",
        request_id="call-private",
        tasks=[{"title": "Private task", "instruction": "Do the private task."}],
    )

    assert calls[0]["_assistant_owner_key"] == owner
    assert owner not in calls[0]["idempotency_key"]
    assert "user-secret-42" not in calls[0]["idempotency_key"]
