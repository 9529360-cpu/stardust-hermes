"""Submission identity is session-local, bounded, and independent of message text."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tui_gateway import server
from tui_gateway.message_idempotency import SubmissionReceipts

TEXT = "long task: keep going until the slow step finishes"


# ── the receipt store ───────────────────────────────────────────────────────

def test_retry_is_atomic_and_errors_can_be_retried():
    receipts = SubmissionReceipts()
    calls = []

    def submit():
        calls.append(1)
        return {"result": {"status": "queued"}}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: receipts.submit("X", submit), range(20)))
    assert len(calls) == 1
    assert sum(not r["result"].get("already_received", False) for r in results) == 1
    assert receipts.submit("Y", submit)["result"] == {"status": "queued"}
    assert len(calls) == 2
    assert receipts.submit("bad", lambda: {"error": {"code": 4009}}).get("error")
    assert receipts.submit("bad", submit)["result"] == {"status": "queued"}
    assert SubmissionReceipts().submit("X", submit)["result"] == {"status": "queued"}


def test_receipts_expire_and_evict_oldest():
    now = [0.0]
    receipts = SubmissionReceipts(capacity=2, ttl=10, clock=lambda: now[0])
    calls = []

    def submit():
        calls.append(1)
        return {"result": {"status": "streaming"}}

    for key in ("X", "Y", "Z", "X"):
        receipts.submit(key, submit)
    assert len(calls) == 4
    assert receipts.submit("X", submit)["result"]["already_received"] is True
    now[0] = 11
    receipts.submit("X", submit)
    assert len(calls) == 5


# ── prompt.submit: a resend must not steer, redirect or interrupt the turn it belongs to ──

class _StalledThread:
    """The turn thread never runs, so the session stays mid-turn: the state a resend lands in."""

    def __init__(self, target=None, daemon=None, **_):
        self.target = target

    def start(self):
        pass


class _Agent:
    _supports_active_turn_redirect = True

    def __init__(self):
        self.calls = []

    def redirect(self, text):
        self.calls.append(("redirect", text))
        return True

    def steer(self, text):
        self.calls.append(("steer", text))
        return True

    def interrupt(self, *_a, **_k):
        self.calls.append(("interrupt",))


@pytest.fixture
def mid_turn(monkeypatch):
    """Submit TEXT as the first message and leave its turn running; yields ``(session, submit)``."""
    monkeypatch.setattr(server.threading, "Thread", _StalledThread)
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(server, "_persist_session_row_for_submit", lambda rid, session: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda sid, session: None)
    monkeypatch.setattr(server, "_restart_completed_failed_agent_build", lambda *a: True)
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: "interrupt")
    session = {
        "agent": _Agent(), "session_key": "session-key", "history": [], "history_lock": threading.Lock(),
        "history_version": 0, "running": False, "transport": None, "attached_images": [],
        "inflight_turn": None, "active_session_lease": object(),
    }
    server._sessions["sid"] = session

    def submit(text=TEXT, message_id=None, sid="sid", **extra):
        params = {"session_id": sid, "text": text, **({"client_message_id": message_id} if message_id else {}), **extra}
        return server._methods["prompt.submit"]("rid", params)

    try:
        yield session, submit
    finally:
        server._sessions.pop("sid", None)


@pytest.mark.parametrize("mode", ["interrupt", "steer", "queue"])
def test_resend_of_an_accepted_message_leaves_the_running_turn_alone(mid_turn, monkeypatch, mode):
    session, submit = mid_turn
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: mode)

    assert submit(message_id="X")["result"] == {"status": "streaming"}
    assert session["running"] and session["inflight_turn"]["user"] == TEXT

    resend = submit(message_id="X")
    assert resend["id"] == "rid"
    assert resend["result"] == {"status": "streaming", "already_received": True}
    assert session["agent"].calls == []
    assert "corrections" not in session["inflight_turn"]
    assert not session.get("queued_prompt")
    assert session["running"]


@pytest.mark.parametrize(
    ("mode", "status"), [("interrupt", "redirected"), ("steer", "steered"), ("queue", "queued")])
def test_identical_text_with_a_new_id_is_a_new_message(mid_turn, monkeypatch, mode, status):
    session, submit = mid_turn
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: mode)
    submit(message_id="X")

    deliberate = submit(message_id="Y")
    assert deliberate["result"]["status"] == status
    assert "already_received" not in deliberate["result"]
    if status == "queued":
        # The legacy self-copy filter must not strip a message the user sent on purpose.
        server._drop_queued_duplicates_of_inflight_user(session)
        assert session["queued_prompt"]["text"] == TEXT
        assert session["queued_prompt"]["client_message_id"] == "Y"
    else:
        assert session["agent"].calls[0][1] == TEXT
        assert session["inflight_turn"]["corrections"]


def test_message_without_an_id_keeps_the_content_check(mid_turn):
    session, submit = mid_turn
    submit()  # an older client: no id on either send

    replay = submit()
    assert replay["result"] == {"status": "streaming"}
    assert session["agent"].calls == []
    assert not session.get("queued_prompt")
    # ... and its text is still a correction when a different author sends it.
    assert submit("something else")["result"]["status"] == "redirected"


def test_receipts_belong_to_one_session(mid_turn):
    session, submit = mid_turn
    other = {**session, "agent": _Agent(), "history_lock": threading.Lock(), "running": False,
             "inflight_turn": None}
    server._sessions["other"] = other
    try:
        assert submit(message_id="X")["result"] == {"status": "streaming"}
        assert submit(message_id="X", sid="other")["result"] == {"status": "streaming"}
        assert other["running"]
    finally:
        server._sessions.pop("other", None)


def test_a_refused_submission_is_not_a_receipt(mid_turn, monkeypatch):
    session, submit = mid_turn
    limits = iter(["too many active sessions", None])
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda sid, s: next(limits))

    assert submit(message_id="X")["error"]["code"] == 4090
    assert not session["running"]
    assert submit(message_id="X")["result"] == {"status": "streaming"}
    assert session["running"]


@pytest.mark.parametrize("bad", ["", "x" * 129, 7, ["X"]])
def test_malformed_id_is_refused_before_anything_happens(mid_turn, bad):
    session, submit = mid_turn

    response = server._methods["prompt.submit"]("rid", {"session_id": "sid", "text": TEXT, "client_message_id": bad})
    assert response["error"]["code"] == 4015
    assert not session["running"]
    assert "submission_receipts" not in session


def test_unknown_session_is_reported_like_any_other_submit(mid_turn):
    _, submit = mid_turn

    assert submit(message_id="X", sid="gone")["error"]["code"] == 4001


def test_the_contract_declares_the_id_and_the_receipt_flag():
    from tui_gateway.contracts.prompt_voice import PromptSubmitParams, PromptSubmitResult

    assert PromptSubmitParams(session_id="s", client_message_id="X").client_message_id == "X"
    assert PromptSubmitResult(status="streaming", already_received=True).already_received is True
    with pytest.raises(ValueError):
        PromptSubmitParams(session_id="s", client_message_id="")
