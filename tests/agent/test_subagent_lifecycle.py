"""Contract tests for the public plugin subagent lifecycle API."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.subagent_lifecycle import (
    SubagentLaunchRequest,
    SubagentLifecycleError,
    SubagentLifecycleService,
    SubagentState,
    bind_subagent_parent,
    get_active_subagent_parent,
)


class FakeChild:
    def __init__(self, ident="sa-test"):
        self._subagent_id = ident
        self._delegate_role = "leaf"
        self._delegate_depth = 1
        self.provider = "test"
        self.model = "test-model"
        self.interrupted = False
        self.interrupt_kind = None
        self.interrupt_message = None
        self.tool_reason = None

    def interrupt(self, _reason):
        self.interrupted = True
        self.interrupt_kind = "soft"

    def hard_interrupt(self, reason, *, tool_reason=None):
        self.interrupted = True
        self.interrupt_kind = "hard"
        self.interrupt_message = reason
        self.tool_reason = tool_reason


@pytest.fixture
def lifecycle(monkeypatch):
    parent = SimpleNamespace(session_id="parent-1", enabled_toolsets=["file"])
    counter = iter(range(1000))

    def build(**_kwargs):
        return FakeChild(f"sa-{next(counter)}")

    def run(_index, _goal, child, _parent):
        for _ in range(20):
            if child.interrupted:
                return {
                    "status": "interrupted",
                    "summary": None,
                    "api_calls": 0,
                    "duration_seconds": 0,
                }
            time.sleep(0.002)
        return {
            "status": "completed",
            "summary": "safe summary",
            "api_calls": 1,
            "duration_seconds": 0.01,
        }

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", build)
    monkeypatch.setattr("tools.delegate_tool._run_single_child", run)
    return SubagentLifecycleService(lambda: parent)






def test_cancel_is_cooperative_and_forged_handle_is_unknown(lifecycle):
    handle = lifecycle.launch(SubagentLaunchRequest(goal="x"))
    assert lifecycle.cancel(handle, reason="test").accepted
    terminal = lifecycle.wait(handle, timeout_seconds=1)
    assert terminal.state is SubagentState.CANCELLED
    forged = handle.__class__(**{**handle.to_dict(), "capability": "forged"})
    assert lifecycle.status(forged).state is SubagentState.UNKNOWN
    assert lifecycle.result(forged).error_classification == "UNKNOWN_HANDLE"
    other_parent = SimpleNamespace(session_id="different-parent")
    other_service = SubagentLifecycleService(lambda: other_parent)
    assert other_service.status(handle).state is SubagentState.UNKNOWN


def test_cancel_uses_explicit_hard_interrupt(lifecycle):
    handle = lifecycle.launch(SubagentLaunchRequest(goal="x"))
    record = lifecycle._record(handle)
    assert record is not None and record.agent is not None

    assert lifecycle.cancel(handle, reason="explicit user cancel").accepted

    assert record.agent.interrupt_kind == "hard"
    assert "explicit user cancel" in record.agent.interrupt_message
    assert record.agent.tool_reason == "subagent cancellation requested"
    lifecycle.wait(handle, timeout_seconds=1)








def test_public_lifecycle_runs_host_aggregation(monkeypatch):
    memory = Mock()
    parent = SimpleNamespace(
        session_id="parent-aggregate",
        enabled_toolsets=["file"],
        _memory_manager=memory,
        _current_turn_id="turn-1",
        session_estimated_cost_usd=1.0,
        session_cost_source="none",
        session_cost_status="unknown",
    )
    child = FakeChild("sa-aggregate")
    child.session_id = "child-session"
    hook = Mock()

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", lambda **_kwargs: child)
    monkeypatch.setattr(
        "tools.delegate_tool._run_single_child",
        lambda *_args, **_kwargs: {
            "task_index": 0,
            "status": "completed",
            "summary": "aggregated",
            "api_calls": 1,
            "duration_seconds": 0.25,
            "_child_role": "leaf",
            "_child_cost_usd": 2.5,
        },
    )
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)

    service = SubagentLifecycleService(lambda: parent)
    handle = service.launch(SubagentLaunchRequest(goal="aggregate me"))
    assert service.wait(handle, timeout_seconds=1).state is SubagentState.SUCCEEDED

    memory.on_delegation.assert_called_once_with(
        task="aggregate me", result="aggregated", child_session_id="child-session"
    )
    hook.assert_called_once_with(
        "subagent_stop",
        parent_session_id="parent-aggregate",
        parent_turn_id="turn-1",
        child_session_id="child-session",
        child_role="leaf",
        child_summary="aggregated",
        child_status="completed",
        # Redacted tool history rides the shared finalization pipeline
        # (#62011/#72403); empty here because the fabricated result carries
        # no tool_trace.
        tool_call_history=[],
        duration_ms=250,
    )
    assert parent.session_estimated_cost_usd == 3.5
    assert parent.session_cost_source == "subagent"
    assert parent.session_cost_status == "estimated"




def test_agent_turn_binds_and_clears_lifecycle_parent(monkeypatch):
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    observed = []

    def run_conversation(parent, *_args, **_kwargs):
        observed.append(get_active_subagent_parent())
        return {"final_response": "ok"}

    monkeypatch.setattr("agent.conversation_loop.run_conversation", run_conversation)

    assert agent.run_conversation("hello") == {"final_response": "ok"}
    assert observed == [agent]
    assert get_active_subagent_parent() is None


def test_duplicate_correlation_is_rejected_while_first_child_is_building(lifecycle, monkeypatch):
    """Two launchers with the same logical key must never construct two children."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()
    built = []

    def slow_build(**_kwargs):
        built.append(True)
        if len(built) == 1:
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release the blocked constructor")
        return FakeChild(f"sa-correlation-race-{len(built)}")

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", slow_build)
    request = SubagentLaunchRequest(goal="one logical task", correlation_id="lifecycle-race-test")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(lifecycle.launch, request)
        assert entered.wait(timeout=2)
        duplicate = executor.submit(lifecycle.launch, request)
        try:
            with pytest.raises(SubagentLifecycleError, match="Duplicate correlation_id"):
                duplicate.result(timeout=2)
        finally:
            release.set()
        handle = first.result(timeout=3)

    assert built == [True], "duplicate launch must not enter the child constructor"
    assert lifecycle.wait(handle, timeout_seconds=2).state is SubagentState.SUCCEEDED


def test_failed_child_construction_releases_correlation_reservation(lifecycle, monkeypatch):
    import tools.delegate_tool as delegate_tool

    original_build = delegate_tool._build_child_agent
    attempts = []

    def fail_first(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("construction failed")
        return original_build(**kwargs)

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", fail_first)
    request = SubagentLaunchRequest(goal="retry after construction failure", correlation_id="lifecycle-build-failure")
    with pytest.raises(RuntimeError, match="construction failed"):
        lifecycle.launch(request)
    handle = lifecycle.launch(request)
    assert lifecycle.wait(handle, timeout_seconds=2).state is SubagentState.SUCCEEDED
    assert len(attempts) == 2


def test_failed_executor_submission_does_not_publish_ghost_child(lifecycle, monkeypatch):
    import agent.subagent_lifecycle as lifecycle_module

    request = SubagentLaunchRequest(goal="can retry after executor refusal", correlation_id="lifecycle-pool-failure")
    with monkeypatch.context() as patcher:
        def refused(*_args, **_kwargs):
            raise RuntimeError("executor unavailable")
        patcher.setattr(lifecycle_module._EXECUTOR, "submit", refused)
        with pytest.raises(RuntimeError, match="executor unavailable"):
            lifecycle.launch(request)
    assert ("parent-1", request.correlation_id) not in lifecycle_module._REGISTRY.correlations
    assert ("parent-1", request.correlation_id) not in lifecycle_module._REGISTRY.pending_correlations
    handle = lifecycle.launch(request)
    assert lifecycle.wait(handle, timeout_seconds=2).state is SubagentState.SUCCEEDED


def test_same_correlation_is_allowed_for_different_parent_sessions(lifecycle):
    from types import SimpleNamespace

    request = SubagentLaunchRequest(goal="scoped task", correlation_id="lifecycle-parent-scope")
    first = lifecycle.launch(request)
    second_parent = SimpleNamespace(session_id="parent-2", enabled_toolsets=["file"])
    other = SubagentLifecycleService(lambda: second_parent)
    second = other.launch(request)
    assert first.parent_session_id != second.parent_session_id
    assert lifecycle.wait(first, timeout_seconds=2).state is SubagentState.SUCCEEDED
    assert other.wait(second, timeout_seconds=2).state is SubagentState.SUCCEEDED
