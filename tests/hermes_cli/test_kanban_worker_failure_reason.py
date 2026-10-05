"""A failing kanban worker's reason reaches the board, and goal mode stops on a failed turn.

Field failure (2026-10-04, maintainer's machine): every background task ran on a model the relay
does not serve. The worker log said so ("No available channel for model gemini-3.8-flash"), but
the board, the crash notifications and the assistant's task listing only ever said "pid N exited
with code 1" / "worker crashed (pid gone)", so the assistant could not tell the user why. A
goal-mode worker meanwhile judged each failed turn's error text and re-ran the same failing call
for its whole 20-turn budget in 13 seconds before blocking the card as "not completed".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import cli
from gateway import kanban_watchers_notifier as notifier
from hermes_cli import goals
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
from tools import assistant_tasks
from tui_gateway.session_notifications import _format_kanban_event_text

_UNSERVED = {
    "failed": True,
    "failure_reason": "model_not_found",
    "error": "HTTP 503: No available channel for model gemini-3.8-flash under group pro (distributor)\nrequest id: x",
}
_DEAD_PID = 424242


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_GOAL_MODE"):
        monkeypatch.delenv(name, raising=False)
    kb._INITIALIZED_PATHS.discard(str(kb.kanban_db_path(board="default").resolve()))
    kb.init_db()
    # The worker's pid is gone when the dispatcher looks (no real process is spawned).
    monkeypatch.setattr(kbd, "_worker_alive", lambda *_args: False)
    with kbc.connect() as conn:
        yield conn


def _running_worker(conn, monkeypatch, **task_fields) -> tuple[str, int]:
    """A claimed task whose worker env is set up the way the dispatcher spawns it."""
    host = kb._claimer_id().split(":", 1)[0]
    tid = kb.create_task(conn, title="Fix the skills page", body="Make it Chinese.", assignee="default",
                         assistant_owner_key="local", **task_fields)
    kb.claim_task(conn, tid, claimer=f"{host}:worker")
    kbd._set_worker_pid(conn, tid, _DEAD_PID)
    conn.execute("UPDATE tasks SET started_at = started_at - 9999 WHERE id = ?", (tid,))
    conn.execute("UPDATE task_runs SET started_at = started_at - 9999 WHERE task_id = ?", (tid,))
    conn.commit()
    run_id = kb._current_run_id(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, run_id


def _run_worker(turns) -> int:
    """Run the quiet worker entry point; each agent turn returns the next result in ``turns``."""
    results = iter(turns)
    calls = []

    def run_conversation(**kwargs):
        calls.append(kwargs["user_message"])
        return dict(next(results))

    agent = SimpleNamespace(run_conversation=run_conversation, session_id="s-1")
    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(SimpleNamespace(agent=agent, conversation_history=[], session_id="s-1"),
                                    "work kanban task")
    _run_worker.calls = calls
    return exc.value.code


def _dispatcher_books_exit(conn, code: int) -> None:
    kbd._record_worker_exit(_DEAD_PID, code << 8)
    kbd.detect_crashed_workers(conn)


def _events(conn, tid, kind):
    return [event for event in kb.list_events(conn, tid) if event.kind == kind]


def test_crash_and_give_up_carry_the_workers_reason(board, monkeypatch):
    tid, run_id = _running_worker(board, monkeypatch)

    assert _run_worker([_UNSERVED]) == 1
    _dispatcher_books_exit(board, 1)

    run = next(r for r in kb.list_runs(board, tid) if r.id == run_id)
    assert "model_not_found: HTTP 503: No available channel for model gemini-3.8-flash" in run.error
    assert "request id" not in run.error  # first line only
    crashed = _events(board, tid, "crashed")[-1]
    assert crashed.payload["failure_reason"] == "model_not_found"
    sub = {"task_id": tid}
    assert "worker failed: model_not_found: HTTP 503" in _format_kanban_event_text(sub, kb.get_task(board, tid),
                                                                                   crashed, "default")
    # The breaker / respawn-guard input keeps the dispatcher's own exit-status text.
    assert kb.get_task(board, tid).last_failure_error == f"pid {_DEAD_PID} exited with code 1"

    # The assistant's listing explains the failure instead of the exit status.
    listing = assistant_tasks._list_tasks(include_completed=False, limit=5, task_ids=[tid], owner_key="local")
    assert "No available channel for model gemini-3.8-flash" in listing

    # Second attempt fails the same way: the give-up notice names the reason.
    _tid, _run = _running_worker_again(board, monkeypatch, tid)
    assert _run_worker([_UNSERVED]) == 1
    _dispatcher_books_exit(board, 1)
    gave_up = _events(board, tid, "gave_up")[-1]
    assert kb.get_task(board, tid).status == "blocked"
    text, _, _ = notifier._fmt_gave_up(gave_up, SimpleNamespace(head=f"Task {tid}", task_id=tid))
    assert "No available channel for model gemini-3.8-flash" in text


def _running_worker_again(conn, monkeypatch, tid) -> tuple[str, int]:
    host = kb._claimer_id().split(":", 1)[0]
    kb.claim_task(conn, tid, claimer=f"{host}:worker-2")
    kbd._set_worker_pid(conn, tid, _DEAD_PID)
    conn.execute("UPDATE tasks SET started_at = started_at - 9999 WHERE id = ?", (tid,))
    conn.execute("UPDATE task_runs SET started_at = started_at - 9999 WHERE task_id = ? AND ended_at IS NULL",
                 (tid,))
    conn.commit()
    run_id = kb._current_run_id(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, run_id


def test_a_worker_never_annotates_a_run_it_does_not_own(board, monkeypatch):
    tid, run_id = _running_worker(board, monkeypatch)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id + 1))  # a stale or foreign run id

    assert _run_worker([_UNSERVED]) == 1

    assert not _events(board, tid, "worker_failed")


@pytest.mark.parametrize(("failure", "exit_code"), [
    (_UNSERVED, 1),
    ({"failed": True, "failure_reason": "overloaded", "error": "HTTP 529 overloaded"}, KANBAN_RATE_LIMIT_EXIT_CODE),
])
def test_goal_mode_stops_on_a_failed_continuation_turn(board, monkeypatch, failure, exit_code):
    tid, run_id = _running_worker(board, monkeypatch, goal_mode=True, goal_max_turns=20)
    monkeypatch.setenv("HERMES_KANBAN_GOAL_MODE", "1")
    monkeypatch.setattr(goals, "judge_goal", lambda *_a, **_k: ("continue", "not done yet", False, None, False))
    working = {"final_response": "Translated half of the catalog.", "completed": True}

    code = _run_worker([working, failure, working, working])

    assert code == exit_code
    assert len(_run_worker.calls) == 2, "the loop must not re-run turns after one failed"
    assert kb.get_task(board, tid).status == "running", "a failed turn is not a goal-loop block"
    note = _events(board, tid, "worker_failed")[-1]
    assert note.run_id == run_id
    assert note.payload["failure_reason"] == failure["failure_reason"]
