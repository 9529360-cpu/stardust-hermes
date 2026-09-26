"""Cancellation wakes the actual callback worker without crossing profile ownership."""

import asyncio
import threading

import pytest

from tools.mcp_dashboard_oauth import DashboardOAuthFlow
from tui_gateway import mcp_oauth_sessions as sessions


@pytest.mark.parametrize("client_redirect", [False, True])
def test_cancel_is_scoped_idempotent_and_releases_worker(
    tmp_path, monkeypatch, client_redirect
):
    monkeypatch.setattr(sessions, "_sessions", {})
    finished = threading.Event()

    def worker(session_id, *_args):
        flow = sessions._sessions[session_id]["flow"]
        try:
            asyncio.run(
                flow.publish_authorization_url(
                    "https://idp.example/authorize?state=test"
                )
            )
            asyncio.run(flow.wait_for_callback(timeout=10))
            flow.mark_approved()
        except RuntimeError as exc:
            flow.mark_error(str(exc))
        finally:
            flow.mark_worker_done()
            finished.set()

    monkeypatch.setattr(sessions, "_worker", worker)
    home = str(tmp_path / "origin")
    monkeypatch.setenv("HERMES_HOME", home)
    result = sessions.start_flow(
        home,
        "reports",
        {"url": "https://mcp.example"},
        client_redirect_uri="http://127.0.0.1:49152/callback"
        if client_redirect
        else None,
    )
    sid = result["session_id"]
    rec = sessions._sessions[sid]
    try:
        assert (
            sessions.cancel_flow(sid, "reports", str(tmp_path / "other"))["ok"] is False
        )
        assert sessions.cancel_flow(sid, "other", home)["ok"] is False
        assert rec["flow"].snapshot()["status"] == "authorization_required"
        assert sessions.cancel_flow(sid, "reports", home)["ok"] is True
        assert finished.wait(5), (
            "cancel must wake the worker, not leave a 5-minute occupied slot"
        )
        assert sessions.poll_flow(sid, "reports")["status"] == "error"
        assert rec["httpd"] is None
        assert sessions.cancel_flow(sid, "reports", home)["ok"] is True
        assert (
            sessions.deliver_callback_flow(sid, "reports", code="late", state="test")[
                "ok"
            ]
            is False
        )
        # A new start can take the per-server slot as soon as the old worker exits.
        finished.clear()
        retry = sessions.start_flow(
            home,
            "reports",
            {"url": "https://mcp.example"},
            client_redirect_uri="http://127.0.0.1:49152/callback",
        )
        assert sessions.cancel_flow(retry["session_id"], "reports", home)["ok"] is True
        assert finished.wait(5)
    finally:
        rec["flow"].mark_error("test cleanup")
        sessions._shutdown_listener(rec)
        finished.wait(5)


def test_start_flow_reserves_server_slot_before_listener_setup(tmp_path, monkeypatch):
    """Duplicate starts cannot both pass the per-server/profile gate while listener setup blocks."""
    monkeypatch.setattr(sessions, "_sessions", {})
    listener_entered = threading.Event()
    release_listener = threading.Event()
    second_done = threading.Event()
    listener_calls = []
    outcomes = {}

    class _Listener:
        server_address = ("127.0.0.1", 49152)

        def shutdown(self):
            return None

        def server_close(self):
            return None

    def listener(flow):
        listener_calls.append(flow.flow_id)
        listener_entered.set()
        assert release_listener.wait(5)
        return _Listener()

    def worker(session_id, *_args):
        flow = sessions._sessions[session_id]["flow"]
        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=test"))
        flow.mark_worker_done()

    monkeypatch.setattr(sessions, "_start_loopback_listener", listener)
    monkeypatch.setattr(sessions, "_worker", worker)
    home = str(tmp_path / "owner")

    def run(label):
        try:
            outcomes[label] = sessions.start_flow(home, "reports", {"url": "https://mcp.example"})
        except Exception as exc:  # captured for cross-thread assertion
            outcomes[label] = exc
        finally:
            if label == "second":
                second_done.set()

    first = threading.Thread(target=run, args=("first",), daemon=True)
    second = threading.Thread(target=run, args=("second",), daemon=True)
    try:
        first.start()
        assert listener_entered.wait(2)
        second.start()
        assert second_done.wait(2), "duplicate start reached listener setup instead of being rejected"
        assert len(listener_calls) == 1
        assert isinstance(outcomes["second"], RuntimeError)
        assert "already in progress" in str(outcomes["second"])
    finally:
        release_listener.set()
        first.join(5)
        second.join(5)

    assert isinstance(outcomes.get("first"), dict)
    assert outcomes["first"]["session_id"]


def test_start_flow_setup_failure_releases_reserved_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(
        sessions,
        "_start_loopback_listener",
        lambda _flow: (_ for _ in ()).throw(OSError("bind failed")),
    )
    home = str(tmp_path / "owner")

    with pytest.raises(OSError, match="bind failed"):
        sessions.start_flow(home, "reports", {"url": "https://mcp.example"})

    assert sessions._sessions == {}


def test_worker_redacts_oauth_failure_before_exposing_flow_error(tmp_path, monkeypatch):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    home = str(tmp_path)
    flow = DashboardOAuthFlow(
        "redact", "reports", None, home, "http://127.0.0.1:49152/callback"
    )
    rec = {
        "flow": flow,
        "server_name": "reports",
        "hermes_home": home,
        "httpd": None,
    }
    monkeypatch.setattr(sessions, "_sessions", {"redact": rec})

    def fail_probe(*_args, **_kwargs):
        raise RuntimeError(
            f"token exchange failed with Authorization: Bearer {secret}"
        )

    monkeypatch.setattr(sessions, "_probe_with_rollback", fail_probe)

    sessions._worker("redact", home, "reports", {"url": "https://mcp.example"}, False)

    error = flow.snapshot()["error"] or ""
    assert secret not in error
    assert "Bearer ***" in error
    assert flow.worker_done is True


@pytest.mark.parametrize("operation", ["poll", "callback", "cancel"])
def test_session_operations_require_resolved_owner(tmp_path, monkeypatch, operation):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = str(tmp_path / "owner")
    flow = DashboardOAuthFlow(
        "owned", "reports", None, home, "http://127.0.0.1:49152/callback"
    )
    asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=test"))
    monkeypatch.setattr(sessions, "_sessions", {
        "owned": {"flow": flow, "server_name": "reports", "hermes_home": home, "httpd": None}
    })

    def invoke():
        if operation == "poll":
            return sessions.poll_flow("owned", "reports")
        if operation == "callback":
            return sessions.deliver_callback_flow("owned", "reports", code="valid", state="test")
        from hermes_constants import get_hermes_home
        return sessions.cancel_flow("owned", "reports", str(get_hermes_home()))

    token = set_hermes_home_override(tmp_path / "other")
    try:
        rejected = invoke()
    finally:
        reset_hermes_home_override(token)
    assert "profile mismatch" in (rejected.get("error_message") or "")
    assert "auth_url" not in rejected
    assert flow.snapshot()["status"] == "authorization_required"
    token = set_hermes_home_override(home)
    try:
        accepted = invoke()
    finally:
        reset_hermes_home_override(token)
    assert accepted.get("ok", accepted.get("status") == "pending") is True


def test_cancel_does_not_revoke_an_approved_flow(tmp_path, monkeypatch):
    home = str(tmp_path)
    flow = DashboardOAuthFlow(
        "approved", "reports", None, home, "http://127.0.0.1:49152/callback"
    )
    flow.mark_approved()
    flow.mark_worker_done()
    monkeypatch.setattr(
        sessions,
        "_sessions",
        {
            "approved": {
                "flow": flow,
                "server_name": "reports",
                "hermes_home": home,
                "httpd": None,
            }
        },
    )
    assert sessions.cancel_flow("approved", "reports", home) == {
        "ok": True,
        "status": "approved",
    }
    assert sessions.cancel_flow("missing", "reports", home)["ok"] is False
