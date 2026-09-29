"""Trace folding for the assistant-scenario exam (``evals/assistant_scenarios``).

The exam grades Stardust from the desktop backend's JSON-RPC event stream. These tests pin how that
stream is folded into a trace: a deferred tool reached through the ``tool_call`` bridge must count as
the tool it invoked, turns must be told apart by who started them (the user vs. a background
completion), and approvals/clarifies keep exactly what the user was shown and answered.
"""
from evals.assistant_scenarios.trace import Trace

SID = "sess-1"


def ev(type_, payload=None, sid=SID):
    return {"type": type_, "session_id": sid, **({"payload": payload} if payload is not None else {})}


def watched():
    trace = Trace()
    trace.watch(SID)
    return trace


def test_bridged_tool_call_counts_as_the_invoked_tool():
    trace = watched()
    trace.ingest(ev("tool.start", {"tool_id": "c1", "name": "tool_call", "args": {
        "name": "cronjob_manage", "arguments": {"action": "create", "schedule": "0 8 * * *"}}}), at=1.0)

    [call] = trace.tool_calls
    assert (call.name, call.args, call.via_bridge) == (
        "cronjob_manage", {"action": "create", "schedule": "0 8 * * *"}, True)


def test_bridged_batch_expands_every_call_and_parses_string_arguments():
    trace = watched()
    trace.ingest(ev("tool.start", {"tool_id": "c2", "name": "tool_call", "args": {"calls": [
        {"name": "todo_list", "arguments": "{\"todos\": []}"},
        {"name": "cronjob_manage", "arguments": {"action": "list"}}]}}), at=1.0)

    assert [(c.name, c.args) for c in trace.tool_calls] == [
        ("todo_list", {"todos": []}), ("cronjob_manage", {"action": "list"})]


def test_completion_attaches_result_to_the_started_call():
    trace = watched()
    trace.ingest(ev("tool.start", {"tool_id": "c3", "name": "terminal", "args": {"command": "python -m unittest"}}), at=1.0)
    trace.ingest(ev("tool.complete", {"tool_id": "c3", "name": "terminal", "result_text": "OK"}), at=2.0)

    [call] = trace.tool_calls
    assert (call.name, call.completed, call.result_text) == ("terminal", True, "OK")


def test_events_from_unwatched_sessions_are_ignored():
    trace = watched()
    trace.ingest(ev("tool.start", {"tool_id": "x", "name": "write_file", "args": {}}, sid="other"), at=1.0)
    trace.ingest(ev("message.complete", {"text": "not ours"}, sid="other"), at=2.0)

    assert (trace.tool_calls, trace.turns) == ([], [])


def test_submitted_turn_is_user_and_unprompted_turn_is_notification():
    trace = watched()
    trace.mark_submit(SID, "你后台研究一下", at=10.0)
    trace.ingest(ev("message.start"), at=10.1)
    trace.ingest(ev("message.start"), at=10.2)  # a follow-up dispatch announces the same turn twice
    trace.ingest(ev("message.complete", {"text": "好的，已交给后台", "status": "complete"}), at=15.0)
    trace.ingest(ev("message.start"), at=60.0)
    trace.ingest(ev("message.complete", {"text": "研究完了：frobnicate", "status": "complete"}), at=70.0)

    assert [(t.origin, t.text) for t in trace.turns] == [
        ("user", "好的，已交给后台"), ("notification", "研究完了：frobnicate")]
    assert [t.duration for t in trace.turns] == [5.0, 10.0]


def test_completion_without_announced_start_still_closes_the_submitted_turn():
    trace = watched()
    trace.mark_submit(SID, "hi", at=1.0)
    trace.ingest(ev("message.complete", {"text": "你好", "status": "complete"}), at=3.0)

    assert [(t.origin, t.text, t.duration) for t in trace.turns] == [("user", "你好", 2.0)]


def test_approval_and_clarify_keep_what_was_shown_and_answered():
    trace = watched()
    trace.record_approval({"session_id": SID, "request_id": "r1", "command": "rm -rf .git",
                           "description": "recursive delete", "tool_name": "terminal"}, choice="deny", at=3.0)
    trace.record_clarify({"session_id": SID, "question": "老王是谁？", "choices": ["微信", "邮件"]},
                         answer="先别发", at=4.0)

    assert (trace.approvals[0].command, trace.approvals[0].choice) == ("rm -rf .git", "deny")
    assert (trace.clarifies[0].question, trace.clarifies[0].answer) == ("老王是谁？", "先别发")


def test_batch_clarify_questions_are_all_kept_for_grading():
    trace = watched()
    trace.record_clarify({"session_id": SID, "questions": [
        {"qid": "q1", "question": "发到哪？"}, {"qid": "q2", "question": "用什么格式？"}]}, answer="", at=1.0)

    assert "发到哪？" in trace.clarifies[0].question and "用什么格式？" in trace.clarifies[0].question


def test_busy_and_last_activity_follow_the_watched_session_only():
    trace = watched()
    assert (trace.busy, trace.last_event_at) == (False, None)
    trace.mark_submit(SID, "hi", at=1.0)
    assert trace.busy is True  # submitted but not yet announced still counts as in flight
    trace.ingest(ev("message.start"), at=2.0)
    trace.ingest(ev("tool.start", {"tool_id": "x", "name": "write_file", "args": {}}, sid="other"), at=9.0)
    assert (trace.busy, trace.last_event_at) == (True, 2.0)
    trace.ingest(ev("message.complete", {"text": "ok"}), at=3.0)
    assert (trace.busy, trace.last_event_at) == (False, 3.0)


def test_child_tool_activity_is_kept_for_grading_delegated_work():
    trace = watched()
    trace.ingest(ev("subagent.tool", {"goal": "fix", "task_count": 1, "task_index": 0,
                                      "tool_name": "terminal", "tool_preview": "python -m unittest discover"}), at=1.0)

    assert trace.subagent_tools == [("terminal", "python -m unittest discover")]
