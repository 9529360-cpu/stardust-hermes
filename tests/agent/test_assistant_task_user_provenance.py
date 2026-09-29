"""Durable-task authorization must come from the current human turn, not machinery."""

from types import SimpleNamespace

from agent.inline_tool_executors import (
    INLINE_TOOL_EXECUTORS,
    InlineToolContext,
    _latest_user_message,
)


def test_plain_current_user_message_is_available():
    assert _latest_user_message([
        {"role": "user", "content": "  继续执行  "},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
    ]) == "继续执行"


def test_steer_current_user_message_keeps_human_authority():
    assert _latest_user_message([
        {"role": "user", "content": "改成方案 B", "display_kind": "steer"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
    ]) == "改成方案 B"


def test_internal_current_turn_never_falls_back_to_old_human_approval():
    messages = [
        {"role": "user", "content": "我批准这一次"},
        {"role": "assistant", "content": "收到"},
        {
            "role": "user",
            "content": "[kanban] task blocked: credential missing",
            "display_kind": "internal_notification",
        },
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_2"}]},
    ]

    assert _latest_user_message(messages) == ""


def test_multimodal_current_human_turn_keeps_only_explicit_text():
    assert _latest_user_message([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请检查这个"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                {"type": "input_text", "text": "然后继续"},
            ],
        }
    ]) == "请检查这个\n然后继续"


def test_assistant_tasks_executor_does_not_receive_synthetic_wake_as_user_input(monkeypatch):
    captured = {}

    def fake_assistant_tasks_tool(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("tools.assistant_tasks.assistant_tasks_tool", fake_assistant_tasks_tool)
    ctx = InlineToolContext(
        effective_task_id="turn-1",
        tool_call_id="call-1",
        messages=[
            {"role": "user", "content": "之前我同意过"},
            {"role": "assistant", "content": "等待后台结果"},
            {
                "role": "user",
                "content": "[kanban] task blocked",
                "display_kind": "internal_notification",
            },
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        ],
    )

    result = INLINE_TOOL_EXECUTORS["assistant_tasks"](
        SimpleNamespace(session_id="session-1"),
        {"action": "resume", "task_id": "task-1"},
        ctx,
    )

    assert result == "ok"
    assert captured["user_message"] == ""
    assert captured["task_id"] == "task-1"
    assert captured["session_id"] == "session-1"
    assert captured["request_id"] == "call-1"
