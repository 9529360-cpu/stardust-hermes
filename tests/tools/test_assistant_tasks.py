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
    keys = [call["idempotency_key"] for call in calls[:3]]
    assert all(key.startswith("assistant:") for key in keys)
    assert [key.rsplit(":", 1)[1] for key in keys] == ["0", "1", "2"]
    assert len({key.rsplit(":", 1)[0] for key in keys}) == 1
    assert all("local" not in key and "call-42" not in key for key in keys)
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

    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert calls[0]["idempotency_key"].startswith("assistant:")
    assert calls[0]["idempotency_key"].endswith(":0")
    assert "tool-call-stable" not in calls[0]["idempotency_key"]
    assert "local" not in calls[0]["idempotency_key"]


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
        def get_task(conn, task_id):
            task = next((row for row in rows if row.id == task_id), None)
            if task is not None:
                task.assistant_owner_key = "local"
            return task

        @staticmethod
        def latest_run(conn, task_id):
            if task_id == "t_wait":
                return SimpleNamespace(summary="Waiting for approval of the exact itinerary and price.")
            return None

    @contextmanager
    def fake_board(_board):
        yield FakeKb, object()

    monkeypatch.setattr("tools.kanban_tools._board", fake_board)

    monkeypatch.setattr(assistant_tasks, "_assistant_board_slugs", lambda: ["default"])

    active = json.loads(
        assistant_tasks.assistant_tasks_tool(action="list", include_completed=False)
    )
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
    for request_id in ("call-private-a", "call-private-b"):
        assistant_tasks.assistant_tasks_tool(
            action="create",
            owner_key=owner,
            session_id="chat-A",
            request_id=request_id,
            tasks=[{"title": "Private task", "instruction": "Do the private task."}],
        )

    assert calls[0]["_assistant_owner_key"] == owner
    assert calls[1]["_assistant_owner_key"] == owner
    assert calls[0]["idempotency_key"] != calls[1]["idempotency_key"]
    for call in calls:
        key = call["idempotency_key"]
        assert owner not in key
        assert "user-secret-42" not in key
        assert "call-private" not in key



def test_list_keeps_newest_owner_tasks_visible_past_200_rows(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    with kbc.connect() as conn:
        for index in range(205):
            task_id = kb.create_task(
                conn,
                title=f"task-{index:03d}",
                assignee="default",
                assistant_owner_key="local",
            )
            conn.execute(
                "UPDATE tasks SET created_at = ? WHERE id = ?",
                (1000 + index, task_id),
            )
        conn.commit()

    listed = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
            limit=5,
        )
    )
    assert [task["title"] for task in listed["tasks"]] == [
        "task-204",
        "task-203",
        "task-202",
        "task-201",
        "task-200",
    ]



def test_list_recalls_owner_tasks_across_active_boards(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    kb.create_board("alpha", name="Alpha")
    kb.create_board("beta", name="Beta")
    with kbc.connect_closing(board="alpha") as conn:
        kb.create_task(
            conn,
            title="Alpha durable task",
            assignee="default",
            assistant_owner_key="local",
        )
        kb.create_task(
            conn,
            title="Other user's alpha task",
            assignee="default",
            assistant_owner_key="messaging:telegram:someone-else",
        )
    with kbc.connect_closing(board="beta") as conn:
        kb.create_task(
            conn,
            title="Beta durable task",
            assignee="default",
            assistant_owner_key="local",
        )

    # The later conversation happens after the user switched boards.
    kb.set_current_board("beta")
    listed = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
            limit=10,
        )
    )

    assert listed["partial"] is False
    assert {(task["board"], task["title"]) for task in listed["tasks"]} == {
        ("alpha", "Alpha durable task"),
        ("beta", "Beta durable task"),
    }


def test_list_reports_partial_snapshot_when_one_board_is_unreadable(monkeypatch):
    row = SimpleNamespace(
        id="t_ok",
        title="Healthy task",
        status="running",
        assignee="default",
        project_id=None,
        workspace_kind="scratch",
        workspace_path=None,
        created_at=10,
        started_at=11,
        completed_at=None,
        block_kind=None,
        last_failure_error=None,
        result=None,
    )

    class FakeKb:
        @staticmethod
        def list_tasks(conn, **kwargs):
            return [row]

        @staticmethod
        def latest_run(conn, task_id):
            return None

    @contextmanager
    def fake_board(board):
        if board == "broken":
            raise OSError("private local path that must not leak")
        yield FakeKb, object()

    monkeypatch.setattr(assistant_tasks, "_assistant_board_slugs", lambda: ["healthy", "broken"])
    monkeypatch.setattr("tools.kanban_tools._board", fake_board)

    listed = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
        )
    )

    assert listed["ok"] is True
    assert listed["partial"] is True
    assert listed["board_errors"] == [{"board": "broken", "error": "OSError"}]
    assert listed["tasks"][0]["board"] == "healthy"
    assert listed["tasks"][0]["title"] == "Healthy task"
    assert "private local path" not in json.dumps(listed)



def test_board_recall_respects_explicit_db_pin(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    kb.create_board("beta", name="Beta")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "pinned.db"))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "beta")
    monkeypatch.setattr(
        kb,
        "list_boards",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not enumerate under a DB pin")),
    )

    assert assistant_tasks._assistant_board_slugs() == ["beta"]



def test_missing_request_id_never_creates_a_stable_owner_replay_key(monkeypatch):
    calls = []
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "tools.kanban_tools._handle_create",
        lambda args: calls.append(dict(args))
        or json.dumps({
            "ok": True,
            "task_id": f"t_{len(calls)}",
            "status": "ready",
            "workspace_kind": "scratch",
            "project_id": None,
            "subscribed": True,
        }),
    )

    for _ in range(2):
        assistant_tasks.assistant_tasks_tool(
            action="create",
            owner_key="messaging:telegram:123456",
            session_id="chat-A",
            tasks=[{"title": "Task", "instruction": "Do it."}],
        )

    first, second = (call["idempotency_key"] for call in calls)
    assert first != second
    assert "123456" not in first
    assert "123456" not in second
    assert "request" not in first
    assert "request" not in second



def test_specific_task_id_lookup_bypasses_recent_200_cap(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    oldest_id = None
    with kbc.connect() as conn:
        for index in range(205):
            task_id = kb.create_task(
                conn,
                title=f"task-{index:03d}",
                assignee="default",
                assistant_owner_key="local",
            )
            if index == 0:
                oldest_id = task_id
            conn.execute(
                "UPDATE tasks SET created_at = ? WHERE id = ?",
                (1000 + index, task_id),
            )
        conn.commit()

    listed = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
            limit=1,
            task_ids=[oldest_id],
        )
    )
    assert listed["partial"] is False
    assert [task["title"] for task in listed["tasks"]] == ["task-000"]


def test_board_discovery_failure_returns_partial_current_board(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    with kbc.connect() as conn:
        kb.create_task(
            conn,
            title="Still visible",
            assignee="default",
            assistant_owner_key="local",
        )

    monkeypatch.setattr(
        kb,
        "list_boards",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("private path must not leak")),
    )
    listed = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
            include_completed=True,
        )
    )

    assert listed["ok"] is True
    assert listed["partial"] is True
    assert listed["board_errors"] == [{"board": "*", "error": "OSError"}]
    assert [task["title"] for task in listed["tasks"]] == ["Still visible"]
    assert "private path" not in json.dumps(listed)



def test_assistant_tasks_hidden_from_scoped_workers(monkeypatch):
    from agent.delegation_context import delegated_child_context

    with delegated_child_context():
        assert assistant_tasks.check_assistant_tasks_requirements() is False
        blocked = json.loads(
            assistant_tasks.assistant_tasks_tool(
                action="list",
                owner_key="local",
            )
        )
        assert "parent user sessions" in blocked["error"]

    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-worker")
    assert assistant_tasks.check_assistant_tasks_requirements() is False
    blocked = json.loads(
        assistant_tasks.assistant_tasks_tool(
            action="list",
            owner_key="local",
        )
    )
    assert "lineage-scoped kanban tools" in blocked["error"]
