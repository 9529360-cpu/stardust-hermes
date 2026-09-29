"""Executable instructions must reach durable work intact, never as a clipped preview."""

import json
from types import SimpleNamespace

import pytest

from tools import assistant_tasks


def _text_at_limit(limit, tail):
    unit = "Read the local notes.\n"
    prefix = (unit * (limit // len(unit) + 1))[:limit - len(tail)]
    return prefix + tail


@pytest.fixture
def create_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(assistant_tasks, "_assistant_tasks_context_allowed", lambda: True)
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(assistant_tasks, "_contains_durable_secret", lambda text: False)

    def create(args):
        calls.append(dict(args))
        return json.dumps({"ok": True, "task_id": f"task-{len(calls)}", "status": "ready"})

    monkeypatch.setattr("tools.kanban_tools._handle_create", create)
    return calls


def _create(tasks):
    return json.loads(assistant_tasks.assistant_tasks_tool(
        action="create", tasks=tasks, owner_key="local", request_id="call-fidelity",
    ))


@pytest.mark.parametrize("field,limit", [
    ("title", assistant_tasks.MAX_TITLE_CHARS),
    ("instruction", assistant_tasks.MAX_INSTRUCTION_CHARS),
])
def test_create_rejects_overflow_instead_of_losing_tail(field, limit, create_calls):
    tail = "Do not publish the result."
    value = _text_at_limit(limit + 1, tail)
    task = {"title": "Research", "instruction": "Read local notes and report."}
    task[field] = value

    result = _create([task])

    assert result["ok"] is False
    assert result["summary"] == {"requested": 1, "created": 0, "failed": 1}
    assert field in result["failed"][0]["error"]
    assert str(limit) in result["failed"][0]["error"]
    assert "exceeds" in result["failed"][0]["error"]
    assert value not in json.dumps(result)
    assert create_calls == []


@pytest.mark.parametrize("value", [None, [], {"instruction": "Do not publish"}, True, 123, " \n "])
def test_create_rejects_non_text_or_empty_instructions(value, create_calls):
    result = _create([{"title": "Research", "instruction": value}])

    assert result["ok"] is False
    assert result["summary"]["created"] == 0
    assert create_calls == []


def test_create_preserves_exact_boundary_and_scans_the_whole_instruction(create_calls, monkeypatch):
    instruction = _text_at_limit(
        assistant_tasks.MAX_INSTRUCTION_CHARS, "Verify the output; do not publish it.",
    )
    title = "\u661f" * assistant_tasks.MAX_TITLE_CHARS
    scanned = []
    monkeypatch.setattr(
        assistant_tasks, "_contains_durable_secret", lambda text: scanned.append(text) or False,
    )

    result = _create([{"title": title, "instruction": instruction}])

    assert result["ok"] is True
    assert create_calls[0]["title"] == title
    assert create_calls[0]["body"] == instruction
    assert scanned == [f"{title}\n{instruction}"]


def test_batch_keeps_valid_siblings_and_reports_unaccepted_input(create_calls):
    result = _create([
        {"title": "First", "instruction": "Read the notes."},
        {"title": "Too long", "instruction": "x" * (assistant_tasks.MAX_INSTRUCTION_CHARS + 1)},
        {"title": "Third", "instruction": "Verify the result."},
    ])

    assert result["ok"] is False
    assert result["summary"] == {"requested": 3, "created": 2, "failed": 1}
    assert [item["index"] for item in result["created"]] == [0, 2]
    assert [item["index"] for item in result["failed"]] == [1]
    assert [item["title"] for item in create_calls] == ["First", "Third"]
    assert [item["idempotency_key"].rsplit(":", 1)[1] for item in create_calls] == ["0", "2"]


def test_resume_rejects_overflow_before_any_board_access(monkeypatch):
    monkeypatch.setattr(assistant_tasks, "_assistant_tasks_context_allowed", lambda: True)

    def unexpected_access():
        pytest.fail("Oversized input must not read or mutate a board")

    monkeypatch.setattr(assistant_tasks, "_assistant_board_slugs", unexpected_access)
    message = _text_at_limit(
        assistant_tasks.MAX_RESUME_MESSAGE_CHARS + 1, "Keep the original files untouched.",
    )
    result = json.loads(assistant_tasks.assistant_tasks_tool(
        action="resume", task_id="blocked-task", owner_key="local", user_message=message,
    ))

    assert "exceeds" in result["error"]
    assert result["input_recorded"] is False
    assert message not in json.dumps(result)


def test_schema_declares_the_same_intake_limits():
    properties = assistant_tasks.ASSISTANT_TASKS_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
    assert properties["title"]["maxLength"] == assistant_tasks.MAX_TITLE_CHARS
    assert properties["instruction"]["maxLength"] == assistant_tasks.MAX_INSTRUCTION_CHARS


def test_intake_and_resume_preserve_tail_in_durable_storage(tmp_path, monkeypatch):
    """Exercise the actual inline dispatch and Kanban DB, not a mocked writer."""
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(assistant_tasks, "_active_profile_name", lambda: "default")
    instruction = _text_at_limit(
        assistant_tasks.MAX_INSTRUCTION_CHARS, "Verify every output file before reporting completion.",
    )
    agent = SimpleNamespace(session_id="fidelity-session")
    ctx = InlineToolContext(
        effective_task_id="fidelity-turn", tool_call_id="fidelity-create",
        messages=[{"role": "user", "content": instruction}],
    )
    created = json.loads(INLINE_TOOL_EXECUTORS["assistant_tasks"](
        agent, {"action": "create", "tasks": [{"title": "Local report", "instruction": instruction}]}, ctx,
    ))
    assert created["ok"] is True
    task_id = created["created"][0]["task_id"]
    with kbc.connect() as conn:
        assert kb.get_task(conn, task_id).body == instruction
        assert kb.block_task(conn, task_id, reason="Which output format?", kind="needs_input")
        comments_before = kb.list_comments(conn, task_id)

    message = _text_at_limit(
        assistant_tasks.MAX_RESUME_MESSAGE_CHARS, "Use plain text and keep the original files untouched.",
    )
    ctx = InlineToolContext(
        effective_task_id="reject-turn", tool_call_id="reject-resume",
        messages=[{"role": "user", "content": message + "x"}],
    )
    rejected = json.loads(INLINE_TOOL_EXECUTORS["assistant_tasks"](
        agent, {"action": "resume", "task_id": task_id}, ctx,
    ))
    assert rejected["input_recorded"] is False
    with kbc.connect() as conn:
        assert kb.get_task(conn, task_id).status == "blocked"
        assert kb.list_comments(conn, task_id) == comments_before

    ctx = InlineToolContext(
        effective_task_id="resume-turn", tool_call_id="accepted-resume",
        messages=[{"role": "user", "content": message}],
    )
    resumed = json.loads(INLINE_TOOL_EXECUTORS["assistant_tasks"](
        agent, {"action": "resume", "task_id": task_id}, ctx,
    ))
    assert resumed["ok"] is True
    with kbc.connect() as conn:
        assert kb.get_task(conn, task_id).status == "ready"
        assert kb.list_comments(conn, task_id)[-1].body == message
