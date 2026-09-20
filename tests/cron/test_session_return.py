import sys

from cron.session_return import capture_local_session_origin
from gateway.session_context import clear_session_vars, set_session_vars


def test_capture_local_session_origin_fails_closed_when_delegation_context_is_unavailable(
    monkeypatch,
):
    tokens = set_session_vars(source="desktop", session_id="desktop-session-1")
    monkeypatch.setitem(sys.modules, "agent.delegation_context", None)
    try:
        assert capture_local_session_origin(None) is None
    finally:
        clear_session_vars(tokens)
