"""Operator-owned Kanban handoffs must stop the worker they displace."""

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _claimed_task(conn, *, review_run: bool, pid: int):
    task_id = kb.create_task(conn, title="operator transition", assignee="builder")
    if review_run:
        assert kb.request_review(
            conn, task_id, summary="ready for review", reviewer="reviewer",
        )
        claimed = kb.claim_review_task(
            conn, task_id, claimer=f"{kb._claimer_id().split(':', 1)[0]}:reviewer",
        )
    else:
        claimed = kb.claim_task(
            conn, task_id, claimer=f"{kb._claimer_id().split(':', 1)[0]}:worker",
        )
    assert claimed is not None
    kbd._set_worker_pid(conn, task_id, pid)
    current = kb.get_task(conn, task_id)
    assert current is not None and current.status == "running"
    return task_id, int(current.current_run_id)


def _apply_transition(conn, transition: str, task_id: str, run_id: int, *, operator: bool):
    expected = None if operator else run_id
    if transition == "done":
        return kb.complete_task(
            conn, task_id, summary="finished", expected_run_id=expected,
        )
    if transition == "blocked":
        return kb.block_task(
            conn, task_id, reason="waiting for input", kind="needs_input",
            expected_run_id=expected,
        )
    if transition == "review":
        return kb.request_review(
            conn, task_id, summary="implementation ready", reviewer="reviewer",
            expected_run_id=expected, force=operator,
        )
    if transition == "changes_requested":
        ok, _detail = kb.request_changes(
            conn, task_id, reason="please revise", expected_run_id=expected,
        )
        return ok
    raise AssertionError(f"unknown transition {transition}")


@pytest.mark.parametrize(
    ("transition", "review_run"),
    [
        ("done", False),
        ("blocked", False),
        ("review", False),
        ("changes_requested", True),
    ],
)
def test_operator_transition_parks_task_when_displaced_worker_will_not_stop(
    kanban_home, monkeypatch, transition, review_run,
):
    conn = kbc.connect()
    try:
        task_id, run_id = _claimed_task(
            conn, review_run=review_run, pid=43000 + run_id_seed(transition),
        )

        monkeypatch.setattr(
            kb,
            "_terminate_reclaimed_worker",
            lambda pid, claim_lock, **_kwargs: {
                "prev_pid": pid,
                "host_local": True,
                "termination_attempted": True,
                "terminated": False,
                "sigkill": True,
            },
        )

        with pytest.raises(kb.WorkerTerminationError, match="parked blocked"):
            _apply_transition(conn, transition, task_id, run_id, operator=True)

        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "blocked"
        assert task.block_kind == "transient"
        assert task.current_run_id is None
        assert task.worker_pid is None
        latest_block = [e for e in kb.list_events(conn, task_id) if e.kind == "blocked"][-1]
        assert latest_block.payload["worker_stop_failed"] is True
        assert latest_block.payload["transition"] == transition
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("transition", "review_run", "final_status"),
    [
        ("done", False, "done"),
        ("blocked", False, "blocked"),
        ("review", False, "review"),
        ("changes_requested", True, "ready"),
    ],
)
def test_operator_transition_stops_displaced_worker_after_commit(
    kanban_home, monkeypatch, transition, review_run, final_status,
):
    conn = kbc.connect()
    try:
        task_id, run_id = _claimed_task(
            conn, review_run=review_run, pid=41000 + run_id_seed(transition),
        )
        calls = []

        def fake_terminate(pid, claim_lock, *, started_at=None, **_kwargs):
            # Operator transition must be durable before the old process is touched.
            assert conn.in_transaction is False
            landed = kb.get_task(conn, task_id)
            assert landed is not None and landed.status == final_status
            calls.append((pid, claim_lock, started_at))
            return {
                "prev_pid": pid,
                "host_local": True,
                "termination_attempted": True,
                "terminated": True,
                "sigkill": False,
            }

        monkeypatch.setattr(kb, "_terminate_reclaimed_worker", fake_terminate)

        assert _apply_transition(
            conn, transition, task_id, run_id, operator=True,
        ) is True
        assert len(calls) == 1

        events = [
            event for event in kb.list_events(conn, task_id)
            if event.kind == "operator_worker_termination"
        ]
        assert len(events) == 1
        assert events[0].payload["transition"] == transition
        assert events[0].payload["terminated"] is True
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("transition", "review_run", "final_status"),
    [
        ("done", False, "done"),
        ("blocked", False, "blocked"),
        ("review", False, "review"),
        ("changes_requested", True, "ready"),
    ],
)
def test_worker_owned_handoff_does_not_kill_its_own_process(
    kanban_home, monkeypatch, transition, review_run, final_status,
):
    conn = kbc.connect()
    try:
        task_id, run_id = _claimed_task(
            conn, review_run=review_run, pid=42000 + run_id_seed(transition),
        )

        def unexpected_terminate(*_args, **_kwargs):
            raise AssertionError("worker-owned handoff tried to terminate its own process")

        monkeypatch.setattr(kb, "_terminate_reclaimed_worker", unexpected_terminate)

        assert _apply_transition(
            conn, transition, task_id, run_id, operator=False,
        ) is True
        landed = kb.get_task(conn, task_id)
        assert landed is not None and landed.status == final_status
        assert not any(
            event.kind == "operator_worker_termination"
            for event in kb.list_events(conn, task_id)
        )
    finally:
        conn.close()


def run_id_seed(transition: str) -> int:
    return {
        "done": 1,
        "blocked": 2,
        "review": 3,
        "changes_requested": 4,
    }[transition]
