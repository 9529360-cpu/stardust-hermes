"""Settling decides when a scenario is over. The backend marks a background result delivered a moment
before the turn that delivers it starts; ending the scenario in that gap throws the report away. These
tests simulate the backend's ledger and events on a clock, without a model or a process.
"""
import time

from evals.assistant_scenarios import runner
from evals.assistant_scenarios.trace import Trace

SID = "s"


def ev(type_, payload=None):
    return {"type": type_, "session_id": SID, **({"payload": payload} if payload is not None else {})}


def row(state, delivery):
    return {"delegation_id": "d1", "state": state, "delivery_state": delivery, "goal": "research"}


def acknowledged_trace():
    trace = Trace()
    trace.watch(SID)
    trace.mark_submit(SID, "你后台研究一下", at=time.monotonic() - 30)
    trace.ingest(ev("message.complete", {"text": "已交给后台"}), at=time.monotonic() - 20)
    return trace


def test_settle_waits_for_the_turn_a_delivered_result_starts():
    trace, start = acknowledged_trace(), time.monotonic()

    def backend_ledger():  # delivery is recorded first; the delivering turn starts half a second later
        elapsed = time.monotonic() - start
        if elapsed < 0.5:
            return [row("completed", "pending")]
        if elapsed >= 1.0 and len(trace.turns) == 1:
            trace.ingest(ev("message.start"), at=time.monotonic())
        if elapsed >= 1.5 and trace.busy:
            trace.ingest(ev("message.complete", {"text": "研究完成：frobnicate"}), at=time.monotonic())
        return [row("completed", "delivered")]

    assert runner.wait_until_settled(trace, backend_ledger, timeout=10, quiet=1.0, poll=0.1) is True
    assert [t.origin for t in trace.turns] == ["user", "notification"]


def test_settle_gives_up_when_background_work_never_finishes():
    trace = acknowledged_trace()
    began = time.monotonic()
    assert runner.wait_until_settled(trace, lambda: [row("running", "pending")], timeout=1.5, quiet=0.2, poll=0.1) is False
    assert time.monotonic() - began >= 1.5
