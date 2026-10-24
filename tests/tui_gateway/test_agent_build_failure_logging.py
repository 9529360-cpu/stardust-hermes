"""Durable logging of agent build failures with session correlation (F4.4 / D4-04).

The production build wrapper's except branch used to only emit ``agent_error`` to
the client — the logs kept no exception class, phase or stack, so a logs-only
diagnosis could not say why a session failed to start. The build branch now also
``logger.exception``s ``phase=agent_build`` with both session ids and the class;
the queue handlers' RedactingFormatter must scrub any secret the exception text
echoes. Client behaviour (the emitted error) is unchanged.
"""

from __future__ import annotations

import logging
import threading

import pytest

from tui_gateway import server

FAKE_SECRET = "sk-F4FAKESECRET0123456789"


def buildable_session(tmp_path, sid: str) -> dict:
    ready = threading.Event()
    session = {
        "agent": None,
        "agent_ready": ready,
        "cwd": str(tmp_path),
        "history": [],
        "history_lock": threading.RLock(),
        "profile_home": str(tmp_path),
        "running": False,
        "session_key": f"stored-{sid}",
    }
    server._sessions[sid] = session
    return session


@pytest.fixture(autouse=True)
def _no_mcp(monkeypatch):
    monkeypatch.setattr("tui_gateway.entry.ensure_mcp_discovery_started", lambda: None)


def test_build_failure_logs_phase_ids_class_and_stack(tmp_path, monkeypatch, caplog):
    sid = "f44-logging"
    session = buildable_session(tmp_path, sid)
    events = []

    def fail_build(*args, **kwargs):
        raise RuntimeError("No inference provider configured")

    monkeypatch.setattr(server, "_make_agent", fail_build)
    monkeypatch.setattr(server, "_emit", lambda *args: events.append(args))
    with caplog.at_level(logging.ERROR, logger="tui_gateway.server"):
        server._start_agent_build(sid, session)
        assert session["agent_ready"].wait(15)
    try:
        records = [r for r in caplog.records if r.name == "tui_gateway.server" and r.levelno >= logging.ERROR]
        assert len(records) == 1
        record = records[0]
        msg = record.getMessage()
        assert "phase=agent_build" in msg
        assert f"runtime_session={sid}" in msg
        assert "session_key=stored-f44-logging" in msg
        assert "class=RuntimeError" in msg
        # Stack captured: the failing frame is the fake build callable.
        import traceback as traceback_mod
        assert record.exc_info and "fail_build" in "".join(
            traceback_mod.format_exception(*record.exc_info))
        # Client behaviour unchanged: exactly one agent_init_failed error event.
        errors = [payload for kind, _, payload in events if kind == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "agent_init_failed"
        assert session["agent_error"] == "No inference provider configured"
    finally:
        server._sessions.pop(sid, None)


def test_build_failure_log_redacts_secrets_in_exception_text(tmp_path, monkeypatch, caplog):
    """A secret echoed by the exception must be scrubbed by the existing redactor
    before the record reaches a file handler."""
    from agent.redact import RedactingFormatter
    import hermes_logging

    sid = "f44-secret"
    session = buildable_session(tmp_path, sid)

    def fail_build(*args, **kwargs):
        raise RuntimeError(f"provider auth rejected key {FAKE_SECRET} for session")

    monkeypatch.setattr(server, "_make_agent", fail_build)
    monkeypatch.setattr(server, "_emit", lambda *args: None)
    with caplog.at_level(logging.ERROR, logger="tui_gateway.server"):
        server._start_agent_build(sid, session)
        assert session["agent_ready"].wait(15)
    try:
        record = next(r for r in caplog.records
                      if r.name == "tui_gateway.server" and r.levelno >= logging.ERROR)
        formatted = RedactingFormatter(hermes_logging._LOG_FORMAT).format(record)
        assert FAKE_SECRET not in formatted
        # This redactor elides token bodies rather than writing a marker.
        assert "sk-F4F...6789" in formatted
        # The correlation fields survive redaction.
        assert "phase=agent_build" in formatted
        assert "class=RuntimeError" in formatted
    finally:
        server._sessions.pop(sid, None)


def test_successful_build_stays_silent(tmp_path, monkeypatch, caplog):
    """A healthy build logs nothing at ERROR (no new noise on the happy path)."""
    sid = "f44-ok"
    session = buildable_session(tmp_path, sid)

    class _Agent:
        session_id = sid

    monkeypatch.setattr(server, "_make_agent", lambda *a, **kw: _Agent())
    monkeypatch.setattr(server, "_wire_session_agent", lambda *a, **kw: False)
    monkeypatch.setattr(server, "_announce_built_agent", lambda *a, **kw: None)
    with caplog.at_level(logging.ERROR, logger="tui_gateway.server"):
        server._start_agent_build(sid, session)
        assert session["agent_ready"].wait(15)
    try:
        assert not [r for r in caplog.records
                    if r.name == "tui_gateway.server" and r.levelno >= logging.ERROR]
        assert session.get("agent_error") is None
    finally:
        server._sessions.pop(sid, None)
