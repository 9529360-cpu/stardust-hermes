"""Durable goal continuation decisions survive reopen without replaying later work."""

import json
import sys
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from hermes_cli.goals import GoalContract, GoalManager, GoalState
from hermes_state import SessionDB


SESSION_ID = "goal-continuation-checkpoint"
META_KEY = f"goal:{SESSION_ID}"
FRESHNESS_SECONDS = 3600
MAX_ATTEMPTS = 3


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(SESSION_ID, source="tui")
    db.append_message(SESSION_ID, "user", "Complete the artifact and its acceptance check.")
    try:
        yield db
    finally:
        db.close()


@contextmanager
def reopened(db):
    """Drop the live handle so recovery must read the on-disk goal and transcript."""
    path = db.db_path
    db.close()
    fresh_db = SessionDB(path)
    try:
        yield fresh_db
    finally:
        fresh_db.close()


def manager(db):
    return GoalManager(SESSION_ID, session_db=db)


def pending(goal_manager, *, max_attempts=MAX_ATTEMPTS):
    return goal_manager.pending_continuation(
        freshness_seconds=FRESHNESS_SECONDS, max_attempts=max_attempts,
    )


def claim(goal_manager, checkpoint, *, max_attempts=MAX_ATTEMPTS):
    return goal_manager.claim_continuation(
        checkpoint, freshness_seconds=FRESHNESS_SECONDS, max_attempts=max_attempts,
    )


def continuing_goal(db, monkeypatch, *, max_turns=5):
    goal_manager = manager(db)
    goal_manager.set("Complete the artifact", max_turns=max_turns)
    judge = Mock(return_value=("continue", "The artifact still needs repair.", False, None, False))
    monkeypatch.setattr("hermes_cli.goals.judge_goal", judge)
    message_id = db.append_message(SESSION_ID, "assistant", "Initial work completed.", timestamp=2000)
    decision = goal_manager.evaluate_after_turn(
        "Initial work completed.", completed_message_id=message_id,
    )
    return goal_manager, message_id, decision, judge


def test_completed_message_id_persists_a_continuation_decision(session_db, monkeypatch):
    goal_manager, message_id, decision, judge = continuing_goal(session_db, monkeypatch)

    assert decision["should_continue"] is True
    judge.assert_called_once()
    stored = json.loads(session_db.get_meta(META_KEY))
    assert stored["continuation_after_message_id"] == message_id
    assert stored["continuation_attempts"] == 0
    assert stored["turns_used"] == 1
    checkpoint = pending(goal_manager)
    assert checkpoint["snapshot"] == session_db.get_meta(META_KEY)
    assert checkpoint["started_at"] == stored["last_turn_at"]
    assert checkpoint["attempts"] == 0


def test_failed_gate_reopens_with_evidence_then_passes_real_acceptance(
    session_db, tmp_path, monkeypatch,
):
    artifact = tmp_path / "result.txt"
    gate_script = tmp_path / "accept_artifact.py"
    gate_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "artifact = Path(__file__).with_name('result.txt')\n"
        "if not artifact.exists() or artifact.read_text(encoding='utf-8') != 'repaired':\n"
        "    print('ACCEPTANCE_FAILED: result.txt must contain repaired')\n"
        "    sys.exit(7)\n"
        "print('ACCEPTANCE_PASSED')\n",
        encoding="utf-8",
    )
    contract = GoalContract(
        outcome="result.txt contains repaired",
        verification="The artifact acceptance command exits zero",
        constraints="Preserve the input.csv source",
        boundaries="Only result.txt may change",
    )
    goal_manager = manager(session_db)
    goal_manager.set("Repair the artifact", max_turns=4, contract=contract)
    goal_manager.add_subgoal("Preserve the row count")
    goal_manager.add_gate(f'"{sys.executable}" "{gate_script}"', timeout_seconds=10)
    judge = Mock(return_value=("done", "The repaired artifact passed acceptance.", False, None, False))
    monkeypatch.setattr("hermes_cli.goals.judge_goal", judge)
    initial_message_id = session_db.append_message(SESSION_ID, "assistant", "First attempt finished.")

    decision = goal_manager.evaluate_after_turn(
        "First attempt finished.", completed_message_id=initial_message_id,
    )

    assert decision["should_continue"] is True
    assert decision["verdict"] == "gate_failed"
    judge.assert_not_called()
    assert goal_manager.state.last_verdict == "gate_failed"
    assert goal_manager.state.last_reason
    assert goal_manager.state.gates[0].last_exit_code == 7
    assert "ACCEPTANCE_FAILED" in goal_manager.state.gates[0].last_output_tail

    with reopened(session_db) as fresh_db:
        recovered = manager(fresh_db)
        checkpoint = pending(recovered)
        assert checkpoint is not None
        assert recovered.state.continuation_after_message_id == initial_message_id
        for evidence in (
            "Repair the artifact", contract.outcome, contract.verification,
            contract.constraints, contract.boundaries, "Preserve the row count",
            "ACCEPTANCE_FAILED: result.txt must contain repaired",
        ):
            assert evidence in checkpoint["prompt"]
        assert claim(recovered, checkpoint) is True

        artifact.write_text("repaired", encoding="utf-8")
        fresh_db.append_message(SESSION_ID, "user", checkpoint["prompt"])
        completed_message_id = fresh_db.append_message(SESSION_ID, "assistant", "Artifact repaired.")
        completed = recovered.evaluate_after_turn(
            "Artifact repaired.", user_initiated=False, completed_message_id=completed_message_id,
        )

        assert completed["status"] == "done"
        assert completed["should_continue"] is False
        judge.assert_called_once()
        assert judge.call_args.kwargs["contract"] == contract
        assert judge.call_args.kwargs["subgoals"] == ["Preserve the row count"]
        durable = manager(fresh_db)
        assert durable.state.turns_used == 2
        assert durable.state.gates[0].last_exit_code == 0
        assert durable.state.continuation_after_message_id == 0
        assert durable.state.continuation_attempts == 0
        assert pending(durable) is None
        assert sum(
            row["id"] == initial_message_id for row in fresh_db.get_messages(SESSION_ID)
        ) == 1


def test_legacy_active_json_loads_without_inventing_a_checkpoint(session_db):
    raw = json.dumps({
        "goal": "Existing goal", "status": "active", "turns_used": 1, "max_turns": 5,
        "last_verdict": "continue", "last_turn_at": time.time(),
        "subgoals": ["Retain the existing requirement"],
    })
    session_db.set_meta(META_KEY, raw)

    state = GoalState.from_json(raw)
    assert state.continuation_after_message_id == 0
    assert state.continuation_attempts == 0
    restored = manager(session_db)
    assert restored.state.subgoals == ["Retain the existing requirement"]
    assert pending(restored) is None
    assert session_db.get_meta(META_KEY) == raw


@pytest.mark.parametrize("newer_role", ["user", "assistant"])
def test_later_message_id_rejects_recovery_even_when_its_clock_goes_backwards(
    session_db, monkeypatch, newer_role,
):
    goal_manager, completed_id, _, _ = continuing_goal(session_db, monkeypatch)
    checkpoint = pending(goal_manager)
    raw = session_db.get_meta(META_KEY)
    newer_id = session_db.append_message(
        SESSION_ID, newer_role, "A later turn supersedes this decision.", timestamp=1,
    )

    assert newer_id > completed_id
    assert pending(manager(session_db)) is None
    assert claim(goal_manager, checkpoint) is False
    assert session_db.get_meta(META_KEY) == raw


@pytest.mark.parametrize("change", ["pause", "new_goal", "new_user_message"])
def test_user_change_during_judging_cannot_be_overwritten(session_db, monkeypatch, change):
    goal_manager = manager(session_db)
    goal_manager.set("Old goal", max_turns=5)
    completed_id = session_db.append_message(SESSION_ID, "assistant", "Old goal turn completed.")
    changed_raw = []

    def judge_with_user_change(*args, **kwargs):
        user_control = manager(session_db)
        if change == "pause":
            user_control.pause("User pressed Stop while judging")
        elif change == "new_goal":
            user_control.set("Replacement goal", max_turns=9)
        else:
            session_db.append_message(SESSION_ID, "user", "Wait for my revised instructions.", timestamp=1)
        changed_raw.append(session_db.get_meta(META_KEY))
        return "continue", "The old judge wants another turn.", False, None, False

    monkeypatch.setattr("hermes_cli.goals.judge_goal", judge_with_user_change)
    decision = goal_manager.evaluate_after_turn("Old goal turn completed.", completed_message_id=completed_id)

    assert decision["should_continue"] is False
    assert session_db.get_meta(META_KEY) == changed_raw[0]
    durable = manager(session_db)
    if change == "pause":
        assert durable.state.status == "paused"
        assert durable.state.paused_reason == "User pressed Stop while judging"
    elif change == "new_goal":
        assert durable.state.goal == "Replacement goal"
        assert durable.state.turns_used == 0
    else:
        assert durable.state.goal == "Old goal"
        assert durable.state.turns_used == 1
    assert durable.state.continuation_after_message_id == 0
    assert pending(durable) is None


def test_evaluation_retires_old_checkpoint_before_a_judge_failure(session_db, monkeypatch):
    goal_manager, _, _, _ = continuing_goal(session_db, monkeypatch)
    checkpoint = pending(goal_manager)
    assert claim(goal_manager, checkpoint) is True
    session_db.append_message(SESSION_ID, "user", checkpoint["prompt"])
    completed_id = session_db.append_message(SESSION_ID, "assistant", "Second attempt completed.")
    observed = []

    def unavailable_judge(*args, **kwargs):
        observed.append(json.loads(session_db.get_meta(META_KEY)))
        raise RuntimeError("judge failed after the completed turn was persisted")

    monkeypatch.setattr("hermes_cli.goals.judge_goal", unavailable_judge)
    with pytest.raises(RuntimeError, match="judge failed"):
        goal_manager.evaluate_after_turn("Second attempt completed.", completed_message_id=completed_id)

    assert observed[0]["continuation_after_message_id"] == 0
    assert observed[0]["continuation_attempts"] == 0
    with reopened(session_db) as fresh_db:
        durable = manager(fresh_db)
        assert durable.state.turns_used == 2
        assert pending(durable) is None


def test_pre_dispatch_crash_retains_checkpoint_but_not_an_unlimited_retry_budget(session_db, monkeypatch):
    goal_manager, completed_id, _, _ = continuing_goal(session_db, monkeypatch)
    first = pending(goal_manager, max_attempts=2)
    assert claim(goal_manager, first, max_attempts=2) is True
    assert claim(goal_manager, first, max_attempts=2) is False

    with reopened(session_db) as fresh_db:
        recovered = manager(fresh_db)
        second = pending(recovered, max_attempts=2)
        assert second["attempts"] == 1
        assert recovered.state.continuation_after_message_id == completed_id
        assert recovered.state.turns_used == 1
        assert claim(recovered, second, max_attempts=2) is True
        assert pending(manager(fresh_db), max_attempts=2) is None
        assert claim(recovered, second, max_attempts=2) is False
        stored = json.loads(fresh_db.get_meta(META_KEY))
        assert stored["continuation_attempts"] == 2
        assert stored["continuation_after_message_id"] == completed_id
        assert stored["turns_used"] == 1


@pytest.mark.parametrize("verdict,wait_directive", [("done", None), ("blocked", None), ("wait", {"seconds": 60})])
def test_non_continuing_decisions_never_leave_a_replayable_checkpoint(
    session_db, monkeypatch, verdict, wait_directive,
):
    goal_manager, _, _, _ = continuing_goal(session_db, monkeypatch)
    next_message_id = session_db.append_message(SESSION_ID, "assistant", "The next turn finished.")
    monkeypatch.setattr(
        "hermes_cli.goals.judge_goal",
        Mock(return_value=(verdict, "The goal should stop or wait.", False, wait_directive, False)),
    )

    decision = goal_manager.evaluate_after_turn("The next turn finished.", completed_message_id=next_message_id)

    assert decision["should_continue"] is False
    durable = manager(session_db)
    assert durable.state.continuation_after_message_id == 0
    assert durable.state.continuation_attempts == 0
    assert pending(durable) is None


def test_exhausted_turn_budget_cannot_become_a_restart_continuation(session_db, monkeypatch):
    goal_manager, _, decision, _ = continuing_goal(session_db, monkeypatch, max_turns=1)

    assert decision["status"] == "paused"
    assert decision["should_continue"] is False
    assert goal_manager.state.continuation_after_message_id == 0
    assert pending(manager(session_db)) is None


def test_expired_checkpoint_is_not_claimed(session_db, monkeypatch):
    goal_manager, _, _, _ = continuing_goal(session_db, monkeypatch)
    checkpoint = pending(goal_manager)
    raw = session_db.get_meta(META_KEY)
    stored = json.loads(raw)
    stored["last_turn_at"] = time.time() - FRESHNESS_SECONDS - 60
    expired_raw = json.dumps(stored)
    session_db.set_meta(META_KEY, expired_raw)

    assert pending(manager(session_db)) is None
    assert claim(goal_manager, checkpoint) is False
    assert session_db.get_meta(META_KEY) == expired_raw
