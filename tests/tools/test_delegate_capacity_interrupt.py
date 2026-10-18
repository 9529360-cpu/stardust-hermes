"""Background pool rejection and safe cancellation ownership."""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from agent.interrupt_control import InterruptControlMixin
from agent.turn_context import _bind_interrupt_scope
from tools import async_delegation
from tools.delegate_tool_dispatch import _Batch, _dispatch_background
from tools.interrupt import is_interrupted, set_interrupt
from tools.process_registry import process_registry


class _Parent(InterruptControlMixin):
    def __init__(self):
        self.session_id = "capacity-interrupt-parent"
        self._active_children = []
        self._active_children_lock = threading.Lock()
        self._execution_thread_id = None
        self._interrupt_requested = False
        self._hard_interrupt_requested = threading.Event()
        self.quiet_mode = True


class _ControlledChild(_Parent):
    """Replace model work while retaining real interrupt and worker-start semantics."""

    def __init__(self):
        super().__init__()
        self.session_id = "capacity-interrupt-child"
        self._delegate_role = "leaf"
        self._delegate_depth = 1
        self._delegate_saved_tool_names = []
        self._credential_pool = None
        self._subagent_id = None
        self.tool_progress_callback = None
        self.model = "test-model"
        self.started = threading.Event()
        self.stop_received = threading.Event()
        self.unwinding = threading.Event()
        self.allow_finish = threading.Event()
        self.finished = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0
        self.closed_while_running = False
        self.observed_interrupt = None

    def interrupt(self, message=None, **kwargs):
        accepted = super().interrupt(message, **kwargs)
        self.stop_received.set()
        return accepted

    def hard_interrupt(self, message=None, **kwargs):
        super().hard_interrupt(message, **kwargs)
        self.stop_received.set()

    def run_conversation(self, **_kwargs):
        # A stop can arrive before this thread exists. Use the real turn-start
        # binding so the pending agent interrupt must reach the tool thread too.
        _bind_interrupt_scope(self, lambda: SimpleNamespace(_set_interrupt=set_interrupt))
        self.started.set()
        try:
            assert self.stop_received.wait(30), "child never received cancellation"
            assert self._interrupt_requested
            assert is_interrupted(), "stop did not reach the child execution thread"
            self.observed_interrupt = (
                self._interrupt_message, self._hard_interrupt_requested.is_set(),
            )
            self.unwinding.set()
            assert self.allow_finish.wait(30), "test did not release child cleanup"
            return {
                "final_response": "", "completed": False, "interrupted": True,
                "api_calls": 0, "messages": [],
            }
        finally:
            self.clear_interrupt()
            self.finished.set()

    def get_activity_summary(self):
        return {"api_call_count": 0}

    def close(self):
        self.closed_while_running |= not self.finished.is_set()
        self.close_count += 1
        self.closed.set()


def _batch(parent, *children):
    tasks = [{"goal": f"wait until cancelled {i}"} for i in range(len(children))]
    parent._active_children.extend(children)
    return _Batch(
        task_list=tasks, children=[(i, tasks[i], child) for i, child in enumerate(children)], parent_agent=parent,
        creds={"model": children[0].model}, context=None, top_role="leaf", max_children=len(children),
        live_deleg_id=None, live_writers=[], live_paths=[], origin_wake_sid="",
        origin_ui_session_id="", origin_owner_transport=None,
        origin_owner_session_record=None, origin_session_history_delivery=False, overall_start=time.monotonic(),
    )


@pytest.fixture
def registry_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.delenv("STARDUST_IGNORE_USER_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text(
        "delegation:\n  max_concurrent_children: 1\n  worktree_isolation: false\n  capacity_fallback_sync: true\n",
        encoding="utf-8",
    )
    async_delegation._reset_for_tests()
    completion_queue = queue.Queue()
    monkeypatch.setattr(process_registry, "completion_queue", completion_queue)
    yield completion_queue
    # Test bodies release their gates and join their workers before registry teardown.
    if async_delegation._executor is not None:
        async_delegation._executor.shutdown(wait=True)
    async_delegation._reset_for_tests()


@pytest.mark.parametrize("rejection", ["capacity", "schedule_failure", "partial_schedule_failure"])
@pytest.mark.parametrize("stop_timing", ["running", "during_admission"])
@pytest.mark.parametrize("stop_kind", ["soft", "hard"])
def test_rejected_background_child_stops_with_parent(
    registry_state, monkeypatch, tmp_path, rejection, stop_timing, stop_kind,
):
    parent, child = _Parent(), _ControlledChild()
    background_child = pending_child = None
    if rejection == "partial_schedule_failure":
        # Three independent units: one accepted, one rejected, one not yet submitted.
        # The model-facing batch width is legal under the configured limit.
        (tmp_path / "config.yaml").write_text(
            "delegation:\n  max_concurrent_children: 3\n  worktree_isolation: false\n"
            "  capacity_fallback_sync: true\n  independent_completions: true\n",
            encoding="utf-8",
        )
        background_child, pending_child = _ControlledChild(), _ControlledChild()
        background_child.session_id += "-background"
        pending_child.session_id += "-pending"
        batch = _batch(parent, background_child, child, pending_child)
    else:
        batch = _batch(parent, child)
    occupied = threading.Event()
    release_occupier = threading.Event()
    admission_started = threading.Event()
    continue_admission = threading.Event()
    outcome = Future()

    def occupy_slot():
        occupied.set()
        assert release_occupier.wait(30)
        return {"status": "completed", "summary": "slot released"}

    if rejection == "capacity":
        accepted = async_delegation.dispatch_async_delegation(
            goal="occupy the only slot", context=None, toolsets=None, role="leaf",
            model=child.model, session_key="other-session", runner=occupy_slot,
            max_async_children=1,
        )
        assert accepted["status"] == "dispatched"
        assert occupied.wait(5)
    elif rejection == "schedule_failure":
        class RejectingExecutor:
            def submit(self, *_args, **_kwargs):
                raise RuntimeError("executor shut down")

        monkeypatch.setattr(async_delegation, "_get_executor", lambda _n: RejectingExecutor())
    else:
        executor = async_delegation._get_executor(3)

        class PartiallyRejectingExecutor:
            submitted = 0

            def submit(self, *args, **kwargs):
                self.submitted += 1
                if self.submitted == 2:
                    raise RuntimeError("unit submission failed")
                return executor.submit(*args, **kwargs)

        partial_executor = PartiallyRejectingExecutor()
        monkeypatch.setattr(async_delegation, "_get_executor", lambda _n: partial_executor)

    dispatch = async_delegation.dispatch_async_delegation_batch
    admissions = 0
    accepted_ids = []

    def pause_admission(**kwargs):
        nonlocal admissions
        admissions += 1
        rejected_admission = 2 if background_child is not None else 1
        if admissions == rejected_admission:
            admission_started.set()
            assert continue_admission.wait(5)
        result = dispatch(**kwargs)
        if result.get("status") == "dispatched":
            accepted_ids.append(result["delegation_id"])
        return result

    monkeypatch.setattr(async_delegation, "dispatch_async_delegation_batch", pause_admission)

    def run_dispatch():
        try:
            outcome.set_result(json.loads(_dispatch_background(batch)))
        except BaseException as exc:
            outcome.set_exception(exc)

    worker = threading.Thread(target=run_dispatch, daemon=True)
    worker.start()
    try:
        assert admission_started.wait(5)
        if background_child is not None:
            assert background_child.started.wait(5)
        request_stop = parent.hard_interrupt if stop_kind == "hard" else parent.interrupt
        stop_message = "user correction or stop request"
        if stop_timing == "during_admission":
            request_stop(stop_message)
        continue_admission.set()
        assert child.started.wait(5)
        if stop_timing == "running":
            request_stop(stop_message)

        assert child.stop_received.wait(5), "fallback lost parent cancellation ownership"
        assert child.unwinding.wait(5)
        assert child.observed_interrupt == (stop_message, stop_kind == "hard")
        if background_child is not None:
            assert not background_child.stop_received.is_set()
            assert pending_child.stop_received.is_set(), "unsubmitted unit lost parent cancellation ownership"
            assert not pending_child.started.is_set()
        assert not outcome.done(), "dispatch returned while its child still owned resources"
        assert child.close_count == 0
        child.allow_finish.set()
        result = outcome.result(timeout=5)
        if background_child is None:
            assert "SYNCHRONOUSLY" in result["note"]
            assert result["results"][0]["status"] == "interrupted"
        else:
            assert result["status"] == "dispatched"
            assert result["inline_results"][0]["status"] == "interrupted"
            assert not background_child.stop_received.is_set()
            assert pending_child.unwinding.wait(5)
            assert pending_child.observed_interrupt == (stop_message, stop_kind == "hard")
        assert child.finished.is_set()
        assert child.close_count == 1
        assert not child.closed_while_running
        assert parent._active_children == []
    finally:
        continue_admission.set()
        if not child.finished.is_set():
            child.hard_interrupt("test teardown")
        child.allow_finish.set()
        worker.join(timeout=5)
        release_occupier.set()
        if background_child is not None:
            async_delegation.interrupt_for_session(parent_session_id=parent.session_id)
            for extra in (background_child, pending_child):
                extra.allow_finish.set()
                assert extra.closed.wait(5)
                assert extra.finished.is_set()
                assert extra.close_count == 1
                assert not extra.closed_while_running
            completed_ids = {registry_state.get(timeout=5)["delegation_id"] for _ in accepted_ids}
            assert completed_ids == set(accepted_ids)
        if rejection == "capacity":
            completion = registry_state.get(timeout=5)
            assert completion["delegation_id"] == accepted["delegation_id"]
        assert not worker.is_alive()


def test_registry_cancel_of_parallel_batch_emits_one_unknown_completion_and_defers_cleanup(
    registry_state, monkeypatch, tmp_path,
):
    import tools.delegate_tool_dispatch as dispatcher

    (tmp_path / "config.yaml").write_text(
        "delegation:\n  max_concurrent_children: 2\n  worktree_isolation: false\n"
        "  independent_completions: false\n  capacity_fallback_sync: false\n",
        encoding="utf-8",
    )
    parent, children = _Parent(), [_ControlledChild(), _ControlledChild()]
    children[1].session_id += "-second"
    batch = _batch(parent, *children)
    monkeypatch.setattr(dispatcher, "_SYNC_INTERRUPT_GRACE_SECONDS", 0.05)
    finalized_indexes = []
    monkeypatch.setattr(
        dispatcher, "_finalize_child_results",
        lambda entries, *_args: finalized_indexes.extend(entry["task_index"] for entry in entries),
    )

    def run_child(index, _task, agent):
        agent.started.set()
        assert agent.stop_received.wait(5)
        assert agent.allow_finish.wait(5)
        agent.finished.set()
        agent.close()
        return {"task_index": index, "status": "interrupted"}

    batch.run_child = run_child
    try:
        dispatch = json.loads(_dispatch_background(batch))
        assert dispatch["status"] == "dispatched"
        assert all(child.started.wait(5) for child in children)

        assert async_delegation.interrupt_for_session(parent_session_id=parent.session_id) == 1
        completion = registry_state.get(timeout=3)
        assert completion["delegation_id"] == dispatch["delegation_id"]
        assert completion["status"] == "error"
        assert [entry["status"] for entry in completion["results"]] == ["unknown", "unknown"]
        assert not finalized_indexes
        assert all(child.close_count == 0 and not child.finished.is_set() for child in children)

        for child in children:
            child.allow_finish.set()
        assert all(child.closed.wait(5) for child in children)
        deadline = time.monotonic() + 5
        while len(finalized_indexes) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(finalized_indexes) == [0, 1]
        assert all(child.close_count == 1 and not child.closed_while_running for child in children)
        assert registry_state.empty(), "late worker exit must not emit a duplicate batch completion"
    finally:
        async_delegation.interrupt_for_session(parent_session_id=parent.session_id)
        for child in children:
            child.allow_finish.set()
            if not child.finished.is_set():
                child.hard_interrupt("test teardown")


def test_async_cancel_returns_unknown_without_finalizing_live_worker(monkeypatch, tmp_path):
    import tools.delegate_tool_dispatch as dispatcher
    import tools.delegation_live_log as live_log
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    default_home = tmp_path / "default"
    request_home = tmp_path / "request-profile"
    monkeypatch.setenv("STARDUST_HOME", str(default_home))
    delegation_id = "deleg_async_cancel"
    tasks = [{"goal": "wait until cancelled 0"}]
    live_log.create_live_transcripts(tasks, delegation_id=delegation_id)
    profile_token = set_hermes_home_override(request_home)
    try:
        live_log.create_live_transcripts(tasks, delegation_id=delegation_id)
    finally:
        reset_hermes_home_override(profile_token)

    parent, child = _Parent(), _ControlledChild()
    batch = _batch(parent, child)
    batch.unit_id = "async-cancel-test"
    batch.live_deleg_id = delegation_id
    monkeypatch.setattr(dispatcher, "_SYNC_INTERRUPT_GRACE_SECONDS", 0.05)
    finalizations = []
    manifest_updates = []
    monkeypatch.setattr(dispatcher, "_finalize_child_results", lambda *_args: finalizations.append("finalized"))
    update_manifest_statuses = live_log.update_manifest_statuses

    def record_manifest_update(updated_id, entries):
        update_manifest_statuses(updated_id, entries)
        manifest_updates.append((updated_id, entries, live_log.live_transcript_root()))

    monkeypatch.setattr(live_log, "update_manifest_statuses", record_manifest_update)

    def run_child(_index, _task, agent):
        agent.started.set()
        assert agent.stop_received.wait(5)
        assert agent.allow_finish.wait(5)
        agent.finished.set()
        agent.close()
        return {"task_index": 0, "status": "interrupted"}

    batch.run_child = run_child
    outcome = Future()

    def execute_batch():
        token = set_hermes_home_override(request_home)
        try:
            outcome.set_result(dispatcher._execute_and_aggregate(batch, honor_parent_interrupt=False))
        except BaseException as exc:
            outcome.set_exception(exc)
        finally:
            reset_hermes_home_override(token)

    worker = threading.Thread(target=execute_batch, daemon=True)
    worker.start()
    try:
        assert child.started.wait(5)
        batch.cancel_event.set()
        worker.join(timeout=2)
        assert not worker.is_alive(), "single-child async cancellation left the aggregate waiting"
        result = outcome.result(timeout=1)
        assert result["results"][0]["status"] == "unknown"
        assert result["results"][0]["cancellation_pending"] is True
        assert not child.finished.is_set()
        assert child.close_count == 0
        assert finalizations == []
        assert manifest_updates == []

        child.allow_finish.set()
        assert child.closed.wait(5)
        assert child.finished.is_set()
        deadline = time.monotonic() + 5
        while not manifest_updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert finalizations == ["finalized"]
        assert len(manifest_updates) == 1
        assert manifest_updates[0][0] == delegation_id
        assert manifest_updates[0][1][0]["status"] == "interrupted"
        assert manifest_updates[0][2] == request_home / "cache" / "delegation" / "live"
        request_manifest = json.loads(
            (request_home / "cache" / "delegation" / "live" / delegation_id / "manifest.json")
            .read_text(encoding="utf-8")
        )
        default_manifest = json.loads(
            (default_home / "cache" / "delegation" / "live" / delegation_id / "manifest.json")
            .read_text(encoding="utf-8")
        )
        assert request_manifest["tasks"][0]["status"] == "interrupted"
        assert default_manifest["tasks"][0]["status"] == "running"
        assert child.close_count == 1
        assert not child.closed_while_running
    finally:
        batch.cancel_event.set()
        child.allow_finish.set()
        if not child.finished.is_set():
            child.hard_interrupt("test teardown")
        worker.join(timeout=5)


def test_parallel_async_cancel_bounds_wait_and_defers_each_child_finalization(monkeypatch):
    import tools.delegate_tool_dispatch as dispatcher

    parent, children = _Parent(), [_ControlledChild(), _ControlledChild()]
    children[1].session_id += "-second"
    batch = _batch(parent, *children)
    batch.unit_id = "async-parallel-cancel-test"
    monkeypatch.setattr(dispatcher, "_SYNC_INTERRUPT_GRACE_SECONDS", 0.05)
    finalized_indexes = []
    monkeypatch.setattr(
        dispatcher, "_finalize_child_results",
        lambda entries, *_args: finalized_indexes.extend(entry["task_index"] for entry in entries),
    )

    def run_child(index, _task, agent):
        agent.started.set()
        assert agent.stop_received.wait(5)
        assert agent.allow_finish.wait(5)
        agent.finished.set()
        agent.close()
        return {"task_index": index, "status": "interrupted"}

    batch.run_child = run_child
    outcome = Future()

    def execute_batch():
        try:
            outcome.set_result(dispatcher._execute_and_aggregate(batch, honor_parent_interrupt=False))
        except BaseException as exc:
            outcome.set_exception(exc)

    worker = threading.Thread(target=execute_batch, daemon=True)
    worker.start()
    try:
        assert all(child.started.wait(5) for child in children)
        batch.cancel_event.set()
        worker.join(timeout=2)
        assert not worker.is_alive(), "parallel async cancellation left aggregate waiting"
        result = outcome.result(timeout=1)
        assert [entry["status"] for entry in result["results"]] == ["unknown", "unknown"]
        assert all(entry["cancellation_pending"] for entry in result["results"])
        assert not finalized_indexes
        assert all(not child.finished.is_set() and child.close_count == 0 for child in children)

        for child in children:
            child.allow_finish.set()
        assert all(child.closed.wait(5) for child in children)
        deadline = time.monotonic() + 5
        while len(finalized_indexes) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(finalized_indexes) == [0, 1]
        assert all(child.close_count == 1 and not child.closed_while_running for child in children)
    finally:
        batch.cancel_event.set()
        for child in children:
            child.allow_finish.set()
            if not child.finished.is_set():
                child.hard_interrupt("test teardown")
        worker.join(timeout=5)


def test_async_cancel_racing_with_child_exit_keeps_real_result_and_finalizes_once(monkeypatch):
    import tools.delegate_tool_dispatch as dispatcher

    parent, child = _Parent(), _ControlledChild()
    batch = _batch(parent, child)
    batch.unit_id = "async-cancel-race-test"
    monkeypatch.setattr(dispatcher, "_SYNC_INTERRUPT_GRACE_SECONDS", 1.0)
    finalizations = []
    monkeypatch.setattr(dispatcher, "_finalize_child_results", lambda results, *_args: finalizations.append(results))

    def run_child(_index, _task, agent):
        agent.started.set()
        assert agent.stop_received.wait(5)
        assert agent.allow_finish.wait(5)
        agent.finished.set()
        agent.close()
        return {"task_index": 0, "status": "interrupted", "exit_reason": "interrupted"}

    batch.run_child = run_child
    outcome = Future()

    def execute_batch():
        try:
            outcome.set_result(dispatcher._execute_and_aggregate(batch, honor_parent_interrupt=False))
        except BaseException as exc:
            outcome.set_exception(exc)

    worker = threading.Thread(target=execute_batch, daemon=True)
    worker.start()
    try:
        assert child.started.wait(5)
        batch.cancel_event.set()
        assert child.stop_received.wait(5)
        child.allow_finish.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        result = outcome.result(timeout=1)
        assert result["results"] == [{"task_index": 0, "status": "interrupted", "exit_reason": "interrupted"}]
        assert len(finalizations) == 1
        assert finalizations[0][0]["status"] == "interrupted"
        assert child.closed.wait(5)
        assert child.close_count == 1
        assert not child.closed_while_running
    finally:
        batch.cancel_event.set()
        child.allow_finish.set()
        if not child.finished.is_set():
            child.hard_interrupt("test teardown")
        worker.join(timeout=5)


def test_accepted_background_child_keeps_registry_cancellation_ownership(registry_state):
    parent, child = _Parent(), _ControlledChild()
    try:
        result = json.loads(_dispatch_background(_batch(parent, child)))
        assert result["status"] == "dispatched"
        assert child.started.wait(5)
        parent.interrupt()
        # Parent interrupt fan-out is synchronous; observing it return establishes
        # that a detached child did not receive it without a timing-based wait.
        assert parent._interrupt_requested
        assert not child.stop_received.is_set()
        assert not child.finished.is_set()
        parent.hard_interrupt("stop the current parent turn")
        assert parent._hard_interrupt_requested.is_set()
        assert not child.stop_received.is_set()
        assert async_delegation.interrupt_for_session(parent_session_id=parent.session_id) == 1
        assert child.unwinding.wait(5)
        assert child.observed_interrupt[1] is True
        assert child.close_count == 0
        child.allow_finish.set()
        completion = registry_state.get(timeout=5)
        assert completion["delegation_id"] == result["delegation_id"]
        assert completion["results"][0]["status"] == "interrupted"
        assert child.finished.is_set()
        assert child.close_count == 1
        assert not child.closed_while_running
        assert parent._active_children == []
    finally:
        if not child.finished.is_set():
            child.hard_interrupt("test teardown")
        child.allow_finish.set()
        assert child.closed.wait(5)


def test_capacity_rejection_does_not_run_children_in_foreground(registry_state, monkeypatch):
    """Default policy preserves responsiveness and reclaims unstarted children."""
    from tools import delegate_tool_dispatch as dispatch
    parent, child = _Parent(), _ControlledChild()
    batch = _batch(parent, child)
    occupied, release = threading.Event(), threading.Event()

    def blocker():
        occupied.set()
        assert release.wait(10)
        return {"status": "completed", "summary": "released"}

    handle = async_delegation.dispatch_async_delegation(
        goal="occupy", context=None, toolsets=None, role="leaf", model="test",
        session_key="other", runner=blocker, max_async_children=1,
    )
    assert handle["status"] == "dispatched"
    assert occupied.wait(3)
    monkeypatch.setattr(dispatch, "_resolve_async_wake_sid", lambda *_args: "")
    monkeypatch.setattr(dispatch, "_capacity_fallback_sync", lambda: False)
    try:
        started = time.monotonic()
        outcome = json.loads(_dispatch_background(batch))
        assert time.monotonic() - started < 3
        assert outcome["status"] == "rejected"
        assert outcome["retryable"] is True
        assert outcome["results"][0]["exit_reason"] == "not_started"
        assert not child.started.is_set()
        assert child.closed.is_set()
        assert not parent._active_children
    finally:
        release.set()


def test_parent_cancellation_never_waits_forever_for_child_cleanup(registry_state, monkeypatch):
    """A stuck worker is UNKNOWN and finishes its own cleanup after release."""
    from tools import delegate_tool_dispatch as dispatch
    parent, child = _Parent(), _ControlledChild()
    batch = _batch(parent, child)
    monkeypatch.setattr(dispatch, "_SYNC_INTERRUPT_GRACE_SECONDS", 0.05)
    outcome = []
    finalized_entries = []
    finalized = threading.Event()
    real_finalize = dispatch._finalize_child_results

    def track_finalization(entries, *args, **kwargs):
        finalized_entries.extend(entries)
        if entries:
            finalized.set()
        return real_finalize(entries, *args, **kwargs)

    monkeypatch.setattr(dispatch, "_finalize_child_results", track_finalization)

    worker = threading.Thread(
        target=lambda: outcome.append(dispatch._execute_and_aggregate(batch)),
        daemon=True,
    )
    worker.start()
    try:
        assert child.started.wait(5)
        parent.hard_interrupt("stop stuck delegated job")
        assert child.stop_received.wait(5)
        assert child.unwinding.wait(5)
        worker.join(3)
        assert not worker.is_alive(), "cancelled batch must not block on unresponsive worker"
        assert len(outcome) == 1
        assert outcome[0]["results"][0]["status"] == "unknown"
        assert outcome[0]["results"][0]["cancellation_pending"] is True
        assert outcome[0]["results"][0]["exit_reason"] == "termination_unconfirmed"
        assert not child.closed.is_set()
        assert finalized_entries == []
    finally:
        child.allow_finish.set()
        worker.join(3)
        assert child.finished.wait(5)
        assert child.closed.wait(5)
        assert finalized.wait(5)
        assert finalized_entries[0]["status"] == "interrupted"


def _ledger_locked(_record):
    raise sqlite3.OperationalError("database is locked")


def test_admission_persistence_failure_is_a_rejection_that_reclaims_children(registry_state, monkeypatch):
    """The durable dispatch record is the admission gate. When it cannot be written the unit is rejected and
    its children are returned; the error must not be raised past a caller that already detached them."""
    from tools import delegate_tool_dispatch as dispatch

    parent, child = _Parent(), _ControlledChild()
    batch = _batch(parent, child)
    monkeypatch.setattr(async_delegation, "_persist_dispatch", _ledger_locked)
    monkeypatch.setattr(dispatch, "_resolve_async_wake_sid", lambda *_args: "")
    monkeypatch.setattr(dispatch, "_capacity_fallback_sync", lambda: False)

    outcome = json.loads(_dispatch_background(batch))

    assert outcome["status"] == "rejected"
    assert outcome["retryable"] is True
    assert outcome["reason"] == "async_admission_failed"
    assert "database is locked" in outcome["error"]
    assert outcome["results"][0]["exit_reason"] == "not_started"
    assert not child.started.is_set()
    assert child.close_count == 1, "a rejected child was left open"
    assert parent._active_children == [], "a rejected child was left detached from parent cancellation"
    assert async_delegation.active_count() == 0


def test_admission_failure_of_a_later_unit_keeps_earlier_units_running(registry_state, monkeypatch, tmp_path):
    """Units of one call are admitted one by one. A refusal of unit N must not turn the units that were
    already admitted into a failed call: they run and deliver, the refused unit is reported unstarted."""
    from tools import delegate_tool_dispatch as dispatch

    (tmp_path / "config.yaml").write_text(
        "delegation:\n  max_concurrent_children: 3\n  worktree_isolation: false\n"
        "  independent_completions: true\n",
        encoding="utf-8",
    )
    parent = _Parent()
    running, refused = _ControlledChild(), _ControlledChild()
    running.session_id += "-running"
    refused.session_id += "-refused"
    batch = _batch(parent, running, refused)
    real_persist = async_delegation._persist_dispatch
    attempts = []

    def lock_second_unit(record):
        attempts.append(record["delegation_id"])
        if len(attempts) == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_persist(record)

    monkeypatch.setattr(async_delegation, "_persist_dispatch", lock_second_unit)
    monkeypatch.setattr(dispatch, "_resolve_async_wake_sid", lambda *_args: "")
    monkeypatch.setattr(dispatch, "_capacity_fallback_sync", lambda: False)
    try:
        outcome = json.loads(_dispatch_background(batch))

        assert outcome["status"] == "dispatched"
        assert [entry["exit_reason"] for entry in outcome["inline_results"]] == ["not_started"]
        assert running.started.wait(5)
        assert not refused.started.is_set()
        assert refused.close_count == 1
        assert running.close_count == 0, "an admitted unit lost its child"
        assert parent._active_children == []
        assert async_delegation.active_count() == 1
    finally:
        async_delegation.interrupt_for_session(parent_session_id=parent.session_id)
        running.allow_finish.set()
        assert running.closed.wait(5)
        assert registry_state.get(timeout=5)["delegation_id"] == attempts[0]


def test_admission_failure_under_legacy_sync_opt_in_names_the_real_cause(registry_state, monkeypatch):
    from tools import delegate_tool_dispatch as dispatch

    class QuickChild(_ControlledChild):
        def run_conversation(self, **_kwargs):
            self.started.set()
            self.finished.set()
            return {"final_response": "ran inline", "completed": True, "api_calls": 0, "messages": []}

    parent, child = _Parent(), QuickChild()
    monkeypatch.setattr(async_delegation, "_persist_dispatch", _ledger_locked)
    monkeypatch.setattr(dispatch, "_resolve_async_wake_sid", lambda *_args: "")
    monkeypatch.setattr(dispatch, "_capacity_fallback_sync", lambda: True)

    outcome = json.loads(_dispatch_background(_batch(parent, child)))

    assert outcome["results"][0]["status"] == "completed"
    assert "SYNCHRONOUSLY" in outcome["note"]
    assert "capacity" not in outcome["note"], "the pool was not full; the note must not blame capacity"
    assert "dispatch record" in outcome["note"]
    assert child.close_count == 1
    assert parent._active_children == []
