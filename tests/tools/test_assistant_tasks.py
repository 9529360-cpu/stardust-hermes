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
    assert calls[0]["idempotency_key"] == "assistant:session-7:call-42:0"
    assert calls[1]["idempotency_key"] == "assistant:session-7:call-42:1"
    assert calls[2]["idempotency_key"] == "assistant:session-7:call-42:2"
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
        "assistant:s1:tool-call-stable:0",
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
