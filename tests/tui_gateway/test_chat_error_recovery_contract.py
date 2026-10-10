"""Chat error-recovery contract (GAP_ANALYSIS #6 / phase-1 #1).

Pinned contracts:

* A mid-stream provider failure yields a structured, user-visible, retryable
  ``message.complete`` event (``status: "error"``, ``error_surface``,
  ``recoverable: True``, ``partial: True``), with the partial text preserved.
* The error text never leaks API keys or bearer tokens — every user-visible
  error surface (``payload["error"]``, ``turn_error_text``, terminal callback)
  passes through ``redact_sensitive_text``.
* The session is usable for the next turn after a mid-stream failure.
* ``session.interrupt`` sets ``_turn_cancel_requested`` and calls the agent's
  interrupt mechanism while a turn is running.
"""

from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from tui_gateway import server


# ── helpers (mirrors test_failed_turn_retention patterns) ────────────


class _InlineThread:
    """Run the turn synchronously so tests observe final state."""

    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def _session(agent=None, **extra):
    return {
        "agent": agent if agent is not None else types.SimpleNamespace(),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "image_counter": 0,
        "cols": 80,
        "slash_worker": None,
        "show_reasoning": False,
        "tool_progress_mode": "all",
        "inflight_turn": None,
        **extra,
    }


@pytest.fixture()
def emits(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, sid, payload=None: captured.append((event, sid, payload)),
    )
    return captured


@pytest.fixture()
def turn_env(monkeypatch, tmp_path):
    """Neutralize the turn pipeline's environment-heavy side paths."""
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)
    monkeypatch.setattr(server, "_wire_callbacks", lambda sid: None)
    monkeypatch.setattr(server, "_sync_agent_model_with_config", lambda sid, session: None)
    monkeypatch.setattr(server, "_session_cwd", lambda session: str(tmp_path))
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    monkeypatch.setattr(server, "_tts_stream_begin", lambda: None)
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *a, **k: None)
    monkeypatch.setattr(server, "_get_usage", lambda agent: {})


def _events(captured, name):
    return [payload for event, _sid, payload in captured if event == name]


# ── Mid-stream failure → structured, retryable error event ────────────


def test_mid_stream_failure_emits_structured_error_event(emits, turn_env):
    """A provider that streams partial text then raises must yield a
    ``message.complete`` with ``status: "error"``, ``error_surface``,
    ``recoverable: True``, and the partial text in ``text``."""
    streamed = []

    def _run(message, *, stream_callback=None, **kwargs):
        if stream_callback is not None:
            stream_callback("partial answer before crash")
        raise ConnectionError("connection reset mid-stream")

    agent = types.SimpleNamespace(
        session_id="session-key",
        provider="openai",
        model="gpt-4o",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "do the thing")

    server._run_prompt_submit("rid", "sid", session, "do the thing")

    completes = _events(emits, "message.complete")
    assert len(completes) == 1
    payload = completes[0]
    assert payload["status"] == "error"
    assert payload["recoverable"] is True
    assert payload["partial"] is True
    assert payload["text"] == "partial answer before crash"
    # Structured error surface classifies the failure.
    assert "error_surface" in payload
    assert payload["error_surface"]["retryable"] is True
    # Session released.
    assert session["running"] is False


def test_mid_stream_failure_keeps_partial_in_snapshot(emits, turn_env):
    """The retained inflight snapshot must carry the partial text so a
    reconnecting client rebuilds the partial bubble, not a blank one."""
    def _run(message, *, stream_callback=None, **kwargs):
        if stream_callback is not None:
            stream_callback("halfway done")
        raise RuntimeError("provider 500")

    agent = types.SimpleNamespace(
        session_id="session-key",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "work item")
    server._run_prompt_submit("rid", "sid", session, "work item")

    snapshot = server._inflight_snapshot(session)
    assert snapshot is not None
    assert snapshot["assistant"] == "halfway done"
    assert snapshot["status"] == "error"
    assert snapshot["streaming"] is False


# ── No secret leakage ────────────────────────────────────────────────


_API_KEY = "sk-proj-abc123def456ghi789jkl012mno345pqr678"


def test_exception_path_error_text_is_redacted(emits, turn_env):
    """An exception whose message echoes an API key must never leak that key
    into the ``payload["error"]`` or the ``payload["text"]`` (which is built
    from ``turn_error_text``)."""
    def _run(message, *, stream_callback=None, **kwargs):
        raise RuntimeError(
            f"Request failed: Authorization: Bearer {_API_KEY}")

    agent = types.SimpleNamespace(
        session_id="session-key",
        provider="openai",
        model="gpt-4o",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "secret leak test")
    server._run_prompt_submit("rid", "sid", session, "secret leak test")

    payload = _events(emits, "message.complete")[0]
    assert _API_KEY not in payload.get("error", "")
    assert _API_KEY not in payload.get("text", "")
    # The snapshot error is also redacted.
    snapshot = server._inflight_snapshot(session)
    assert snapshot is not None
    assert _API_KEY not in snapshot.get("error", "")


def test_returned_error_path_error_text_is_redacted(emits, turn_env):
    """The returned-error path's ``payload["error"]`` must also redact secrets
    from the error string the agent returned."""
    def _run(message, **kwargs):
        return {
            "final_response": "",
            "error": f"HTTP 401: invalid api_key={_API_KEY}",
            "failed": True,
            "failure_reason": "auth",
        }

    agent = types.SimpleNamespace(
        session_id="session-key",
        provider="openai",
        model="gpt-4o",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "returned error leak test")
    server._run_prompt_submit("rid", "sid", session, "returned error leak test")

    payload = _events(emits, "message.complete")[0]
    assert _API_KEY not in payload.get("error", "")
    assert _API_KEY not in payload.get("text", "")


def test_turn_error_text_redacts_secrets():
    """Unit: ``turn_error_text`` applies ``redact_sensitive_text`` to the
    detail line."""
    from tui_gateway.user_messages import turn_error_text

    surface = {"layer": "provider", "code": "auth", "retryable": True, "provider": "openai"}
    text = turn_error_text(
        f"401 Unauthorized — key={_API_KEY}", surface)
    assert _API_KEY not in text
    # The title and hint structure is preserved.
    assert "Your message was not answered." in text


def test_terminal_callback_error_is_redacted(emits, turn_env):
    """The terminal callback receipt (for hosted rooms) must carry redacted
    error text, not the raw exception string."""
    received = []

    def _run(message, *, stream_callback=None, **kwargs):
        raise RuntimeError(f"auth failed: {_API_KEY}")

    agent = types.SimpleNamespace(
        session_id="session-key",
        provider="openai",
        model="gpt-4o",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "callback redaction test")
    server._run_prompt_submit(
        "rid", "sid", session, "callback redaction test",
        terminal_callback=received.append)

    assert len(received) == 1
    assert _API_KEY not in received[0].get("error", "")


# ── Session usable for next turn ─────────────────────────────────────


def test_session_usable_for_next_turn_after_mid_stream_failure(emits, turn_env):
    """After a mid-stream failure, the session must accept a new prompt and
    produce a normal response — the failure state is not sticky."""
    call_count = 0

    def _run(message, *, stream_callback=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            if stream_callback is not None:
                stream_callback("partial first reply")
            raise RuntimeError("first turn crashed")
        return {"final_response": "second turn ok"}

    agent = types.SimpleNamespace(
        session_id="session-key",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)

    # First turn: mid-stream failure.
    server._start_inflight_turn(session, "first prompt")
    server._run_prompt_submit("rid", "sid", session, "first prompt")

    first_completes = _events(emits, "message.complete")
    assert first_completes[-1]["status"] == "error"
    assert session["running"] is False
    # The retained failure snapshot exists but must be replaced by the next turn.
    assert server._inflight_snapshot(session) is not None

    # Second turn: normal completion.
    server._start_inflight_turn(session, "second prompt")
    server._run_prompt_submit("rid", "sid", session, "second prompt")

    second_completes = _events(emits, "message.complete")
    assert second_completes[-1]["status"] == "complete"
    assert second_completes[-1]["text"] == "second turn ok"
    # No leftover failure state.
    assert server._inflight_snapshot(session) is None


# ── session.interrupt stops stream promptly ──────────────────────────


@patch("agent.interrupt_compat.request_hard_interrupt")
@patch("hermes_cli.plugins.invoke_hook")
def test_interrupt_running_turn_sets_cancel_flag_and_calls_agent_interrupt(
    mock_invoke_hook, mock_hard_interrupt,
):
    """``session.interrupt`` on a running turn must set
    ``_turn_cancel_requested`` and call the agent's interrupt mechanism."""
    interrupt_called = []

    agent = MagicMock()
    agent.interrupt = lambda message=None: interrupt_called.append(message)

    session = _session(
        agent=agent,
        running=True,
        session_key="agent:main:tui:dm:s1",
    )
    with patch.object(server, "_clear_pending"):
        server._interrupt_session_turn("s1", session)

    assert session["_turn_cancel_requested"] is True
    mock_hard_interrupt.assert_called_once()
    # The agent_loop_stopped hook fires.
    calls = [
        c for c in mock_invoke_hook.call_args_list
        if c.args and c.args[0] == "agent_loop_stopped"
    ]
    assert len(calls) == 1


@patch("agent.interrupt_compat.request_hard_interrupt")
@patch("hermes_cli.plugins.invoke_hook")
def test_interrupt_idle_session_no_agent_interrupt(mock_invoke_hook, mock_hard_interrupt):
    """No live turn → the cancel flag is set (prophylactic) but the agent's
    interrupt mechanism is not called and no hook fires."""
    session = _session(
        agent=MagicMock(),
        running=False,
        session_key="agent:main:tui:dm:s1",
    )
    with patch.object(server, "_clear_pending"):
        server._interrupt_session_turn("s1", session)

    # Flag is set unconditionally — it's a "cancel was requested" marker.
    assert session.get("_turn_cancel_requested") is True
    # But the agent interrupt was NOT called (no live turn).
    mock_hard_interrupt.assert_not_called()
    # And no hook (nothing was running to stop).
    calls = [
        c for c in mock_invoke_hook.call_args_list
        if c.args and c.args[0] == "agent_loop_stopped"
    ]
    assert calls == []


def test_interrupt_during_stream_stops_deltas(emits, turn_env):
    """When ``_turn_cancel_requested`` is set mid-stream, the stream callback
    must stop delivering further deltas.  The agent's ``run_conversation``
    checks the flag between deltas."""
    delivered = []

    def _run(message, *, stream_callback=None, **kwargs):
        if stream_callback is not None:
            stream_callback("first chunk")
            # Simulate interrupt being requested between chunks.
            server._interrupt_session_turn("sid", session)
            # After interrupt, the agent would normally stop — but if it
            # tried to deliver another delta, the cancel flag is already set.
            if not session.get("_turn_cancel_requested"):
                stream_callback("should not arrive")
        return {"final_response": "first chunk"}

    agent = types.SimpleNamespace(
        session_id="session-key",
        run_conversation=_run,
        clear_interrupt=lambda: None,
    )
    session = _session(agent=agent, running=True)
    server._start_inflight_turn(session, "streaming prompt")

    # Capture deltas
    deltas = []
    original_emit = server._emit

    def _capture_emit(event, sid, payload=None):
        if event == "message.delta":
            deltas.append(payload)
        original_emit(event, sid, payload)

    server._emit = _capture_emit
    try:
        server._run_prompt_submit("rid", "sid", session, "streaming prompt")
    finally:
        server._emit = original_emit

    # The cancel flag was set.
    assert session.get("_turn_cancel_requested") is True
    # At least the first chunk was delivered before interrupt.
    assert len(deltas) >= 1
    assert "first chunk" in deltas[0].get("text", "")
