"""First terminal transition wins across every server-request completion path."""
import threading

import pytest

from tui_gateway import server_requests as sr


@pytest.fixture(autouse=True)
def requests(monkeypatch):
    sr.reset_for_tests()
    monkeypatch.setattr(sr, "_write", lambda frame: None)
    monkeypatch.setattr(sr, "_emit", lambda *args: None)
    yield
    sr.reset_for_tests()


def opened(*, callback=None, qids=None):
    req = sr.ServerRequest("session", "clarify", {}, on_result=callback, qids=qids)
    with sr._lock:
        sr._open[req.id] = req
    return req


def test_first_answer_wins_before_sender_cleanup():
    req = opened()
    assert sr.resolve_response({"id": req.id, "result": {"answer": "first"}})
    assert not sr.resolve_response({"id": req.id, "result": {"answer": "second"}})
    assert req.result == {"answer": "first"}
    assert sr.cancel("session") == 0
    assert req.state == "answered"


def test_cancel_cannot_overwrite_answer_while_callback_is_running():
    entered, release = threading.Event(), threading.Event()
    results = []

    def callback(result):
        # Another thread must be able to acquire the registry lock while here.
        entered.set()
        assert release.wait(2)
        results.append(result)

    req = opened(callback=callback)
    thread = threading.Thread(target=sr.resolve_response,
                              args=({"id": req.id, "result": {"answer": "first"}},))
    thread.start()
    try:
        assert entered.wait(2)
        assert req.event.wait(2)
        assert sr.cancel("session") == 0
        assert not sr.resolve_response({"id": req.id, "result": {}})
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert results == [{"answer": "first"}]
    assert req.state == "answered"


def test_last_batch_lock_is_terminal_and_callback_runs_once_outside_lock():
    results = []

    def callback(result):
        assert sr._lock.acquire(blocking=False)
        sr._lock.release()
        results.append(result)

    req = opened(callback=callback, qids=["q"])
    assert sr.lock_answer(req.id, "q", "first") == []
    assert sr.lock_answer(req.id, "q", "second") is None
    assert sr.cancel("session") == 0
    assert not sr.resolve_response({"id": req.id, "result": {"answers": {"q": "second"}}})
    assert req.result == {"answers": {"q": "first"}}
    assert results == [req.result]


def test_cancel_commits_before_callback_and_survives_callback_exception(caplog):
    results = []

    def callback(result):
        assert sr._lock.acquire(blocking=False)
        sr._lock.release()
        results.append(result)
        raise RuntimeError("callback failure")

    req = opened(callback=callback)
    other = opened(callback=results.append)
    assert sr.cancel("session") == 2
    assert req.event.is_set() and other.event.is_set()
    assert req.state == other.state == "cancelled"
    assert results == [None, None]
    assert "callback failure" in caplog.text


def test_timeout_does_not_override_answer_after_wait_returns_false(monkeypatch):
    # Pin the deadline/answer boundary without wall-clock sleeps.
    original = sr.ServerRequest
    captured = []

    class DeadlineRequest(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(self)
            self.event = type("DeadlineEvent", (), {
                "wait": lambda event, timeout: (
                    sr.resolve_response({"id": self.id, "result": {"answer": "won"}}), False
                )[1],
                "set": lambda event: None,
            })()

    monkeypatch.setattr(sr, "ServerRequest", DeadlineRequest)
    assert sr.send("clarify", "session", {"question": "q"}, timeout=0) == {"answer": "won"}
    assert captured[0].state == "answered"


@pytest.mark.parametrize("reason,state", [("timeout", "timed_out"), ("interrupted", "cancelled")])
def test_async_settle_calls_callback_once_and_rejects_late_answer(reason, state, monkeypatch):
    frames, results, events = [], [], []
    monkeypatch.setattr(sr, "_write", frames.append)
    monkeypatch.setattr(sr, "_emit", lambda *args: events.append(args))
    settle = sr.send_async("approval", "session", {"request_id": "a", "command": "test"}, results.append)
    req = sr._open[frames[0]["id"]]
    settle(reason)
    settle(reason)
    assert results == [None]
    assert req.state == state and req.event.is_set()
    assert len(events) == 1
    assert not sr.resolve_response({"id": req.id, "result": {"choice": "once"}})
