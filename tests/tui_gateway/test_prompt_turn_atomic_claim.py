"""F1.2: claiming a session turn is atomic.

Two submits that both observed an idle session must not both start a turn. The claim
(check ``running`` + set ``running``) is one ``history_lock`` critical section; the loser gets
``_TURN_BUSY`` and the caller takes the existing busy path (``_handle_busy_submit``).
"""

import threading

import pytest

# Functions in methods_prompt are rebound onto ``server`` at install time (method_ctx.bind_module);
# only the bound copies see ``is_truthy_value`` and the other server globals, so test through server.
from tui_gateway import server as methods_prompt


def _session(**extra):
    return {
        "history_lock": threading.Lock(),
        "history": [],
        "history_version": 0,
        "running": False,
        "transport": None,
        "attached_images": [],
        "session_key": "k",
        **extra,
    }


def test_claim_on_idle_session_marks_running_once():
    session = _session()
    err, _fields = methods_prompt._lock_in_submit_turn(
        1, "s", session, "hi", {}, False, None, None)
    assert err is None
    assert session["running"] is True


def test_claim_on_running_session_is_refused_without_mutating():
    session = _session(running=True, _turn_cancel_requested=True)
    err, fields = methods_prompt._lock_in_submit_turn(
        2, "s", session, "second", {}, False, None, None)
    assert err is methods_prompt._TURN_BUSY
    assert fields == {}
    # The live turn's state is untouched: no second claim, no cancel flag reset, no inflight write.
    assert session["running"] is True
    assert session["_turn_cancel_requested"] is True
    assert "_inflight" not in session or not session.get("_inflight")


def test_concurrent_claims_on_idle_session_yield_exactly_one_winner():
    """Many threads race the claim on one idle session: exactly one wins, the rest see _TURN_BUSY."""
    for _ in range(200):
        session = _session()
        barrier = threading.Barrier(8)
        results = []
        results_lock = threading.Lock()

        def claim(i):
            barrier.wait()
            err, _f = methods_prompt._lock_in_submit_turn(
                i, "s", session, f"t{i}", {}, False, None, None)
            with results_lock:
                results.append(err)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if r is None]
        busy = [r for r in results if r is methods_prompt._TURN_BUSY]
        assert len(winners) == 1, results
        assert len(busy) == 7, results
        assert session["running"] is True


def test_claim_is_reusable_after_turn_completes():
    session = _session()
    assert methods_prompt._lock_in_submit_turn(
        1, "s", session, "a", {}, False, None, None)[0] is None
    # Turn ends (running cleared under the lock, as the turn runner does).
    with session["history_lock"]:
        session["running"] = False
    assert methods_prompt._lock_in_submit_turn(
        2, "s", session, "b", {}, False, None, None)[0] is None
    assert session["running"] is True


@pytest.mark.parametrize("hosted", [False, True])
def test_race_loser_uses_existing_busy_policy(monkeypatch, hosted):
    session = _session(agent=None, active_session_lease=object())
    sid = "atomic-race"
    monkeypatch.setitem(methods_prompt._sessions, sid, session)
    monkeypatch.setattr(methods_prompt, "_session_uses_compute_host", lambda *a: False)
    monkeypatch.setattr(methods_prompt, "_hosted_submit_error", lambda *a: None)
    seen = []

    def lost_claim(*args):
        session["running"] = True
        return methods_prompt._TURN_BUSY, {}

    def busy(*args, **kwargs):
        seen.append(kwargs)
        return {"jsonrpc": "2.0", "id": 1, "result": {"status": "queued"}}

    monkeypatch.setattr(methods_prompt, "_lock_in_submit_turn", lost_claim)
    monkeypatch.setattr(methods_prompt, "_handle_busy_submit", busy)
    params = {"session_id": sid, "text": "loser", "queued": True}
    if hosted:
        params["_hosted_task"] = {"task_id": "task"}
    result = methods_prompt._accept_prompt_submit(1, params)
    if hosted:
        assert result["error"]["code"] == 4091
        assert seen == []
    else:
        assert result["result"]["status"] == "queued"
        assert seen[0]["queued"] is True


def test_race_loser_retries_when_winning_turn_already_finished(monkeypatch):
    session = _session(agent=None, active_session_lease=object())
    monkeypatch.setitem(methods_prompt._sessions, "atomic-retry", session)
    monkeypatch.setattr(methods_prompt, "_session_uses_compute_host", lambda *a: False)
    claims = []
    response = {"jsonrpc": "2.0", "id": 1, "error": {"code": 4009, "message": "stopped"}}

    def claim(*args):
        claims.append(1)
        return (methods_prompt._TURN_BUSY if len(claims) == 1 else response), {}

    monkeypatch.setattr(methods_prompt, "_lock_in_submit_turn", claim)
    monkeypatch.setattr(methods_prompt, "_handle_busy_submit", lambda *a, **k: None)
    assert methods_prompt._accept_prompt_submit(
        1, {"session_id": "atomic-retry", "text": "retry"}) == response
    assert len(claims) == 2

