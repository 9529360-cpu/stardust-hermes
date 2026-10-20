"""Verify-on-stop tells the model about an unverified workspace state once, not once per turn.

A long conversation made of many short turns (a user message, a background-task notice, a subagent report)
resets the per-turn nudge counter every time. When the workspace state has not changed since the model was
last told, another nudge carries no new information: it only forces another full-context round and leaves
one more near-identical candidate answer in the transcript.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.turn_context import _PER_TURN_RESET_STATE
from agent.turn_stop_gates import apply_stop_gates
from agent.verification_evidence import mark_workspace_edited, record_terminal_result


@pytest.fixture(autouse=True)
def _ledger_on(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / ".stardust"))


def _project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".git").mkdir()  # keeps project discovery inside tmp_path
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    return root


def _agent(session_id: str = "s1") -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        _session_messages=None,
        _flush_messages_to_session_db=lambda messages, history: True,
        _emit_interim_assistant_message=lambda message: None,
        _interim_content_was_streamed=lambda text: False,
    )


def _end_turn(agent, *roots: Path, text: str = "all done"):
    """One turn that ends with a candidate answer after the model's commands flagged ``roots`` as changed.

    Starts from the real per-turn reset table, exactly as a new user message or a background-task notice does.
    """
    for name, value in _PER_TURN_RESET_STATE:
        setattr(agent, name, value)
    agent._turn_file_mutation_paths = {str(root) for root in roots}
    messages = [{"role": "user", "content": "go"}]
    return apply_stop_gates(
        agent, {"role": "assistant", "content": text}, final_response=text, messages=messages,
        conversation_history=[], pending_verification_response=None, pending_verification_response_previewed=None,
    )


def test_an_unchanged_unverified_state_is_nudged_once_not_once_per_turn(tmp_path):
    root = _project(tmp_path)
    mark_workspace_edited(session_id="s1", cwd=root, paths=[str(root / "src" / "app.ts")])
    agent = _agent()

    assert _end_turn(agent, root).continue_turn is True
    for _ in range(3):  # later turns that have nothing new to prove
        assert _end_turn(agent, root).continue_turn is False


def test_a_new_edit_after_the_nudge_arms_the_gate_again(tmp_path):
    root = _project(tmp_path)
    changed = str(root / "src" / "app.ts")
    mark_workspace_edited(session_id="s1", cwd=root, paths=[changed])
    agent = _agent()
    assert _end_turn(agent, root).continue_turn is True
    assert _end_turn(agent, root).continue_turn is False

    mark_workspace_edited(session_id="s1", cwd=root, paths=[changed])  # the same file, edited again

    assert _end_turn(agent, root).continue_turn is True
    assert _end_turn(agent, root).continue_turn is False


def test_a_new_failed_verification_run_is_new_information(tmp_path):
    root = _project(tmp_path)
    mark_workspace_edited(session_id="s1", cwd=root, paths=[str(root / "src" / "app.ts")])
    agent = _agent()
    assert _end_turn(agent, root).continue_turn is True

    record_terminal_result(command="pnpm run test", cwd=root, session_id="s1", exit_code=1, output="1 failed")

    assert _end_turn(agent, root).continue_turn is True
    assert _end_turn(agent, root).continue_turn is False


def test_passing_verification_ends_the_nudging(tmp_path):
    root = _project(tmp_path)
    mark_workspace_edited(session_id="s1", cwd=root, paths=[str(root / "src" / "app.ts")])
    agent = _agent()
    assert _end_turn(agent, root).continue_turn is True

    record_terminal_result(command="pnpm run test", cwd=root, session_id="s1", exit_code=0, output="green")

    assert _end_turn(agent, root).continue_turn is False


def test_another_workspace_that_becomes_unverified_is_not_hidden_behind_the_first(tmp_path):
    first = _project(tmp_path, "first")
    second = _project(tmp_path, "second")
    mark_workspace_edited(session_id="s1", cwd=first, paths=[str(first / "src" / "a.ts")])
    agent = _agent()
    assert _end_turn(agent, first).continue_turn is True
    assert _end_turn(agent, first).continue_turn is False

    mark_workspace_edited(session_id="s1", cwd=second, paths=[str(second / "src" / "b.ts")])

    assert _end_turn(agent, first, second).continue_turn is True
    assert _end_turn(agent, first, second).continue_turn is False


def test_each_agent_remembers_what_it_was_told_on_its_own(tmp_path):
    """The memo is per agent (per conversation process), not a ledger-wide switch."""
    root = _project(tmp_path)
    mark_workspace_edited(session_id="s1", cwd=root, paths=[str(root / "src" / "app.ts")])
    first, second = _agent(), _agent()

    assert _end_turn(first, root).continue_turn is True
    assert _end_turn(second, root).continue_turn is True
    assert _end_turn(first, root).continue_turn is False
