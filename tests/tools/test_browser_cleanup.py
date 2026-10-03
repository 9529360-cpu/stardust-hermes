"""Regression tests for browser session cleanup and screenshot recovery."""

from unittest.mock import patch
from tools import browser_tool_lifecycle as bt_lifecycle


class TestScreenshotPathRecovery:
    def test_extracts_standard_absolute_path(self):
        from tools.browser_tool_snapshot import _extract_screenshot_path_from_text

        assert (
            _extract_screenshot_path_from_text("Screenshot saved to /tmp/foo.png")
            == "/tmp/foo.png"
        )

    def test_extracts_quoted_absolute_path(self):
        from tools.browser_tool_snapshot import _extract_screenshot_path_from_text

        assert (
            _extract_screenshot_path_from_text(
                "Screenshot saved to '/Users/david/.hermes/browser_screenshots/shot.png'"
            )
            == "/Users/david/.hermes/browser_screenshots/shot.png"
        )


class TestBrowserCleanup:
    def setup_method(self):
        from tools import browser_tool

        self.browser_tool = browser_tool
        self.orig_active_sessions = browser_tool._active_sessions.copy()
        self.orig_session_last_activity = browser_tool._session_last_activity.copy()
        self.orig_recording_sessions = browser_tool._recording_sessions.copy()
        self.orig_cleanup_done = browser_tool._cleanup_done

    def teardown_method(self):
        self.browser_tool._active_sessions.clear()
        self.browser_tool._active_sessions.update(self.orig_active_sessions)
        self.browser_tool._session_last_activity.clear()
        self.browser_tool._session_last_activity.update(self.orig_session_last_activity)
        self.browser_tool._recording_sessions.clear()
        self.browser_tool._recording_sessions.update(self.orig_recording_sessions)
        self.browser_tool._cleanup_done = self.orig_cleanup_done

    def test_cleanup_browser_clears_tracking_state(self):
        browser_tool = self.browser_tool
        browser_tool._active_sessions["task-1"] = {
            "session_name": "sess-1",
            "bb_session_id": None,
        }
        browser_tool._session_last_activity["task-1"] = 123.0

        with (
            patch("tools.browser_tool._maybe_stop_recording") as mock_stop,
            patch(
                "tools.browser_tool_session._run_browser_command",
                return_value={"success": True},
            ) as mock_run,
            patch("tools.browser_tool.os.path.exists", return_value=False),
        ):
            bt_lifecycle.cleanup_browser("task-1")

        assert "task-1" not in browser_tool._active_sessions
        assert "task-1" not in browser_tool._session_last_activity
        mock_stop.assert_called_once_with("task-1")
        mock_run.assert_called_once_with("task-1", "close", [], timeout=10)


    def test_emergency_cleanup_clears_all_tracking_state(self):
        browser_tool = self.browser_tool
        browser_tool._cleanup_done = False
        browser_tool._active_sessions["task-1"] = {"session_name": "sess-1"}
        browser_tool._active_sessions["task-2"] = {"session_name": "sess-2"}
        browser_tool._session_last_activity["task-1"] = 1.0
        browser_tool._session_last_activity["task-2"] = 2.0
        browser_tool._recording_sessions.update({"task-1", "task-2"})

        with patch("tools.browser_tool_lifecycle.cleanup_all_browsers") as mock_cleanup_all:
            bt_lifecycle._emergency_cleanup_all_sessions()

        mock_cleanup_all.assert_called_once_with()
        assert browser_tool._active_sessions == {}
        assert browser_tool._session_last_activity == {}
        assert browser_tool._recording_sessions == set()
        assert browser_tool._cleanup_done is True


class TestInactivityJanitorMultiplex:
    """#86402 / #100738: the process-global janitor thread has no profile scope."""

    def setup_method(self):
        from agent import secret_scope
        from tools import browser_tool

        self.bt = browser_tool
        self.saved = {
            name: getattr(browser_tool, name).copy()
            for name in (
                "_active_sessions", "_session_last_activity",
                "_session_owner_homes", "_cleanup_failures", "_recording_sessions",
            )
        }
        self.orig_timeout = browser_tool.BROWSER_SESSION_INACTIVITY_TIMEOUT
        browser_tool.BROWSER_SESSION_INACTIVITY_TIMEOUT = 0
        for name in self.saved:
            getattr(browser_tool, name).clear()
        secret_scope.set_multiplex_active(True)

    def teardown_method(self):
        from agent import secret_scope

        secret_scope.set_multiplex_active(False)
        self.bt.BROWSER_SESSION_INACTIVITY_TIMEOUT = self.orig_timeout
        for name, saved in self.saved.items():
            live = getattr(self.bt, name)
            live.clear()
            live.update(saved)

    def test_janitor_tears_down_under_owner_profile_scope(self, tmp_path, monkeypatch):
        from agent import secret_scope
        from hermes_constants import (
            get_hermes_home, reset_hermes_home_override, set_hermes_home_override,
        )

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("CAMOFOX_URL", raising=False)
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        p1 = tmp_path / "profiles" / "p1"
        p1.mkdir(parents=True)
        (p1 / ".env").write_text("CAMOFOX_URL=http://127.0.0.1:1\n", encoding="utf-8")

        # Profile p1's turn opens the session; the janitor later runs unscoped.
        home_tok = set_hermes_home_override(str(p1))
        scope_tok = secret_scope.set_secret_scope(secret_scope.build_profile_secret_scope(p1))
        try:
            scoped_key = self.bt._registry_session_key("t1")
            bt_lifecycle._update_session_activity(scoped_key)
            self.bt._active_sessions[scoped_key] = {"session_name": "s1", "bb_session_id": None}
        finally:
            secret_scope.reset_secret_scope(scope_tok)
            reset_hermes_home_override(home_tok)
        self.bt._session_last_activity[scoped_key] -= 10

        seen = {}

        def fake_close(task_id, cmd, args, timeout=None):
            seen["home"] = str(get_hermes_home())
            seen["url"] = secret_scope.get_secret("CAMOFOX_URL")
            return {"success": True}

        with (
            patch("tools.browser_tool_session._run_browser_command", side_effect=fake_close),
            patch("tools.browser_camofox._delete", return_value={}),
            patch("tools.browser_tool.os.path.exists", return_value=False),
        ):
            bt_lifecycle._cleanup_inactive_browser_sessions()

        assert seen == {"home": str(p1), "url": "http://127.0.0.1:1"}
        assert scoped_key not in self.bt._session_last_activity
        assert scoped_key not in self.bt._active_sessions
        assert scoped_key not in self.bt._session_owner_homes

    def test_repeated_failures_force_reap_and_close_cloud_session(self):
        from unittest.mock import MagicMock

        self.bt._active_sessions["t1"] = {"session_name": "s1", "bb_session_id": "bb-1"}
        self.bt._session_last_activity["t1"] = 1.0
        provider = MagicMock()

        with (
            patch("tools.browser_tool_lifecycle._cleanup_single_browser_session", side_effect=RuntimeError("boom")),
            patch("tools.browser_tool_cloud._get_cloud_provider", return_value=provider),
            patch("tools.browser_tool.os.path.exists", return_value=False),
        ):
            for _ in range(self.bt.MAX_INACTIVITY_CLEANUP_FAILURES - 1):
                bt_lifecycle._cleanup_inactive_browser_sessions()
            # An activity touch must NOT reset the failure budget.
            bt_lifecycle._update_session_activity("t1")
            self.bt._session_last_activity["t1"] = 1.0
            assert self.bt._cleanup_failures["t1"] == self.bt.MAX_INACTIVITY_CLEANUP_FAILURES - 1
            assert "t1" in self.bt._active_sessions
            provider.close_session.assert_not_called()

            bt_lifecycle._cleanup_inactive_browser_sessions()

        provider.close_session.assert_called_once_with("bb-1")
        assert "t1" not in self.bt._active_sessions
        assert "t1" not in self.bt._session_last_activity
        assert "t1" not in self.bt._cleanup_failures


def test_same_task_id_in_two_profiles_cannot_reuse_or_close_peer_browser(tmp_path, monkeypatch):
    """The process-global browser registry must respect served-profile ownership."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools import browser_tool as bt
    from tools import browser_tool_session as bs

    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first_home.mkdir()
    second_home.mkdir()
    created = []
    closed = []
    saved = {name: getattr(bt, name).copy() for name in (
        "_active_sessions", "_session_last_activity", "_session_owner_homes",
        "_last_active_session_key", "_cleanup_failures", "_suspect_browser_sessions",
    )}
    for name in saved:
        getattr(bt, name).clear()
    monkeypatch.setattr(bt_lifecycle, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(bs, "_create_session_for_key", lambda key, local: (
        created.append({"session_name": f"fake-{len(created)}", "bb_session_id": None}) or created[-1]
    ))
    monkeypatch.setattr(bt_lifecycle, "_session_has_expired", lambda info: False)
    monkeypatch.setattr(bs, "_local_backend_process_dead", lambda info: False)
    monkeypatch.setattr(bt_lifecycle, "_cleanup_single_browser_session", lambda key: closed.append(key))

    try:
        first_token = set_hermes_home_override(str(first_home))
        try:
            first = bs._get_session_info("shared")
        finally:
            reset_hermes_home_override(first_token)
        second_token = set_hermes_home_override(str(second_home))
        try:
            second = bs._get_session_info("shared")
            bt_lifecycle.cleanup_browser("shared")
        finally:
            reset_hermes_home_override(second_token)
        assert second is not first
        first_token = set_hermes_home_override(str(first_home))
        try:
            assert bs._get_session_info("shared") is first
        finally:
            reset_hermes_home_override(first_token)
        assert len(created) == 2
        assert closed and all(key != "shared" for key in closed)
    finally:
        for name, original in saved.items():
            live = getattr(bt, name)
            live.clear()
            live.update(original)


def test_followup_binding_ignores_same_task_in_other_profile(tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools import browser_tool as bt

    saved_sessions = bt._active_sessions.copy()
    saved_bindings = bt._last_active_session_key.copy()
    bt._active_sessions.clear()
    bt._last_active_session_key.clear()
    try:
        first_home = tmp_path / "first"
        second_home = tmp_path / "second"
        first_home.mkdir()
        second_home.mkdir()
        first_token = set_hermes_home_override(str(first_home))
        try:
            primary_key = bt._registry_session_key("shared")
            sidecar_key = bt._registry_session_key("shared::local")
            bt._active_sessions[sidecar_key] = {
                "session_key": "shared::local", "owner_task_id": "shared",
            }
            bt._last_active_session_key[primary_key] = sidecar_key
            assert bt._last_session_key("shared") == "shared::local"
        finally:
            reset_hermes_home_override(first_token)
        second_token = set_hermes_home_override(str(second_home))
        try:
            assert bt._last_session_key("shared") == "shared"
            assert bt._registry_session_key("shared") != primary_key
        finally:
            reset_hermes_home_override(second_token)
    finally:
        bt._active_sessions.clear()
        bt._active_sessions.update(saved_sessions)
        bt._last_active_session_key.clear()
        bt._last_active_session_key.update(saved_bindings)


def test_shutdown_closes_each_profile_under_its_owner_scope(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from tools import browser_tool as bt

    saved = {name: getattr(bt, name).copy() for name in (
        "_active_sessions", "_session_last_activity", "_session_owner_homes",
        "_last_active_session_key", "_cleanup_failures", "_recording_sessions",
    )}
    for name in saved:
        getattr(bt, name).clear()
    seen = []
    monkeypatch.setattr(bt_lifecycle, "_session_has_expired", lambda info: True)
    monkeypatch.setattr(bt_lifecycle, "_is_camofox_mode", lambda: False, raising=False)
    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(bt_lifecycle._cloud, "_get_cloud_provider", lambda: None)
    monkeypatch.setattr(bt_lifecycle, "_release_local_session_artifacts", lambda *args: None)
    monkeypatch.setattr(bt_lifecycle, "_release_session_resources", lambda key, info: (
        seen.append((key, str(get_hermes_home()))), bt_lifecycle._forget_session_tracking(key, session=True)
    ))
    try:
        for name in ("first", "second"):
            home = tmp_path / name
            home.mkdir()
            token = set_hermes_home_override(str(home))
            try:
                key = bt._registry_session_key("shared")
                bt_lifecycle._update_session_activity(key)
                bt._active_sessions[key] = {"session_name": f"{name}-browser", "bb_session_id": None}
            finally:
                reset_hermes_home_override(token)
        bt_lifecycle.cleanup_all_browsers()
        assert len(seen) == 2
        assert {home for _, home in seen} == {str(tmp_path / "first"), str(tmp_path / "second")}
        assert not bt._active_sessions
    finally:
        for name, original in saved.items():
            live = getattr(bt, name)
            live.clear()
            live.update(original)


class TestAtexitStopSwallowsInterrupt:
    def test_second_ctrl_c_during_join_does_not_propagate(self, monkeypatch):
        """A second Ctrl+C while the atexit hook waits on the janitor must not escape as a
        traceback (#10764): the thread is a daemon, the interpreter is already exiting."""
        from tools import browser_tool

        class _InterruptedJoin:
            def join(self, timeout=None):
                raise KeyboardInterrupt

        monkeypatch.setattr(browser_tool, "_cleanup_thread", _InterruptedJoin())
        monkeypatch.setattr(browser_tool, "_cleanup_running", True)
        bt_lifecycle._stop_browser_cleanup_thread()  # must not raise
        assert browser_tool._cleanup_running is False
