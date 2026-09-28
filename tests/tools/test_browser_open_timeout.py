"""Tests for browser first-open timeout and timeout diagnostics."""

import subprocess
from unittest.mock import Mock, patch

import pytest

import tools.browser_tool as bt
from tools import browser_tool_session as bt_session
from tools import browser_tool_lifecycle as bt_lifecycle
from tools import browser_tool_cloud as bt_cloud
from tools import browser_tool_install as bt_install


@pytest.fixture(autouse=True)
def _reset_browser_caches():
    bt._cached_command_timeout = None
    bt._command_timeout_resolved = False
    bt._active_sessions.clear()
    bt._session_last_activity.clear()
    bt._last_active_session_key.clear()
    yield
    bt._cached_command_timeout = None
    bt._command_timeout_resolved = False
    bt._active_sessions.clear()
    bt._session_last_activity.clear()
    bt._last_active_session_key.clear()


class TestOpenCommandTimeout:
    def test_first_open_uses_longer_floor(self, monkeypatch):
        monkeypatch.setattr(bt, "_get_command_timeout", lambda: 30)
        assert bt._get_open_command_timeout(first_open=True) == bt.MIN_FIRST_OPEN_TIMEOUT
        assert bt._get_open_command_timeout(first_open=False) == bt.MIN_OPEN_TIMEOUT

    def test_respects_config_above_floor(self, monkeypatch):
        monkeypatch.setattr(bt, "_get_command_timeout", lambda: 180)
        assert bt._get_open_command_timeout(first_open=True) == 180
        assert bt._get_open_command_timeout(first_open=False) == 180


class TestSandboxBypass:
    def test_docker_triggers_bypass(self, monkeypatch):
        monkeypatch.setattr("tools.browser_tool_install._running_in_docker", lambda: True)
        assert bt_session._needs_chromium_sandbox_bypass() is True

    def test_apparmor_userns_triggers_bypass(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tools.browser_tool_install._running_in_docker", lambda: False)
        sysctl = tmp_path / "apparmor_restrict_unprivileged_userns"
        sysctl.write_text("1\n", encoding="utf-8")

        import builtins

        real_open = builtins.open

        def _open(path, *args, **kwargs):
            if "apparmor_restrict_unprivileged_userns" in str(path):
                return real_open(sysctl, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _open)
        assert bt_session._needs_chromium_sandbox_bypass() is True


class TestTimeoutErrorFormatting:
    def test_includes_stderr_detail(self):
        err = bt_session._format_browser_timeout_error(
            "open",
            120,
            "",
            "Daemon process exited during startup",
        )
        assert "120 seconds" in err
        assert "Daemon process exited" in err


    def test_local_install_hint(self, monkeypatch):
        monkeypatch.setattr("tools.browser_tool_cloud._is_local_mode", lambda: True)
        monkeypatch.setattr("tools.browser_tool_install._running_in_docker", lambda: False)
        err = bt_session._format_browser_timeout_error("open", 60, "", "")
        assert "agent-browser install --with-deps" in err


class TestReadCommandOutputFiles:
    def test_reads_stdout_and_stderr(self, tmp_path):
        stdout_path = tmp_path / "out"
        stderr_path = tmp_path / "err"
        stdout_path.write_text("ok", encoding="utf-8")
        stderr_path.write_text("warn", encoding="utf-8")
        stdout, stderr = bt_session._read_command_output_files(str(stdout_path), str(stderr_path))
        assert stdout == "ok"
        assert stderr == "warn"


class TestCommandTimeoutRecovery:
    @pytest.mark.parametrize("cloud", [False, True])
    def test_timeout_replaces_only_stuck_client(self, monkeypatch, tmp_path, cloud):
        task_id = "stuck-command"
        session_info = {
            "session_name": "stuck-session",
            "bb_session_id": "cloud-session-1" if cloud else None,
            "cdp_url": "ws://cloud.invalid/devtools/browser/1" if cloud else None,
        }
        bt._active_sessions[task_id] = session_info
        bt._session_last_activity[task_id] = 1.0
        bt._last_active_session_key[task_id] = task_id

        process = Mock()
        process.returncode = 0
        process.wait.side_effect = [subprocess.TimeoutExpired("agent-browser", 1), -9, 0]
        supervisor_events = []

        monkeypatch.setattr(bt_install, "_find_agent_browser", lambda: "agent-browser")
        monkeypatch.setattr("tools.browser_tool_install._requires_real_termux_browser_install", lambda _cmd: False)
        monkeypatch.setattr("tools.browser_tool_lifecycle._start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr("tools.browser_tool_cdp._ensure_cdp_supervisor", lambda _: supervisor_events.append("ensure"))
        monkeypatch.setattr("tools.browser_tool_cdp._stop_cdp_supervisor", lambda _: supervisor_events.append("stop"))
        monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
        monkeypatch.setattr("tools.browser_tool_lifecycle._write_owner_pid", lambda *_args: None)
        monkeypatch.setattr(bt, "_build_browser_env", lambda: {})
        monkeypatch.setattr("tools.browser_tool_install._merge_browser_path", lambda value: value)
        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
        monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)

        bt_session._run_browser_command(task_id, "click", ["@e1"], timeout=1)

        assert task_id not in bt._last_active_session_key
        assert not (tmp_path / "agent-browser-stuck-session").exists()
        if not cloud:
            assert task_id not in bt._active_sessions and task_id not in bt._session_last_activity
            return

        replacement = bt._active_sessions[task_id]
        assert replacement is not session_info
        assert replacement["session_name"] != "stuck-session"
        assert replacement["bb_session_id"] == "cloud-session-1"
        assert bt_session._get_session_info(task_id) is replacement

        provider = Mock()
        monkeypatch.setattr(bt_cloud, "_get_cloud_provider", lambda: provider)
        bt_lifecycle.cleanup_browser(task_id)
        provider.close_session.assert_called_once_with("cloud-session-1")
        assert supervisor_events == ["ensure", "stop", "stop"]

    def test_stale_timeout_cannot_remove_concurrent_replacement(self, tmp_path):
        stale, replacement = {"session_name": "stale"}, {"session_name": "replacement"}
        bt._active_sessions["race"] = replacement

        bt_session._discard_timed_out_browser_session("race", stale, str(tmp_path))

        assert bt._active_sessions["race"] is replacement
        assert tmp_path.exists()


class TestBrowserNavigateOpenTimeout:
    def test_first_navigation_uses_first_open_timeout(self, monkeypatch):
        captured: dict = {}

        def fake_run(task_id, command, args, timeout=None):
            if command == "open":
                captured["timeout"] = timeout
            return {"success": True, "data": {"title": "t", "url": args[0] if args else ""}}

        monkeypatch.setattr(bt, "_get_open_command_timeout", lambda first_open=False: 120 if first_open else 60)
        monkeypatch.setattr(bt_session, "_run_browser_command", fake_run)
        monkeypatch.setattr(bt_session, "_get_session_info", lambda key: {"_first_nav": True, "features": {}})
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(bt_cloud, "_is_local_backend", lambda: True)
        monkeypatch.setattr(bt, "_is_local_sidecar_key", lambda key: False)
        monkeypatch.setattr(
            bt, "_navigation_session_key", lambda task_id, url, local_browser=False: task_id
        )
        monkeypatch.setattr(bt, "_maybe_start_recording", lambda *a, **kw: None)
        monkeypatch.setattr(bt, "check_website_access", lambda url: None)

        bt.browser_navigate("https://example.com", task_id="task-1")
        assert captured["timeout"] == 120


class TestManagedLocalChromeFailureCleanup:
    def test_local_open_binds_profile_and_reaps_fatal_launch_failure(
        self, tmp_path, monkeypatch
    ):
        session_info = {
            "session_name": "h_managed123",
            "bb_session_id": None,
            "cdp_url": None,
            "features": {"local": True},
        }
        captured = {}
        discarded = []

        class _Proc:
            returncode = 1

            def wait(self, timeout=None):
                return 1

        def _popen(argv, env, socket_dir, tag):
            captured["env"] = dict(env)
            captured["socket_dir"] = socket_dir
            (tmp_path / f"_stdout_{tag}").write_text(
                '{"success":false,"error":"Chrome exited early without writing DevToolsActivePort"}',
                encoding="utf-8",
            )
            (tmp_path / f"_stderr_{tag}").write_text("", encoding="utf-8")
            return _Proc()

        monkeypatch.setattr(bt_session, "_prepare_session_socket_dir", lambda _name: str(tmp_path))
        monkeypatch.setattr(bt_session, "_agent_browser_command_env", lambda _dir: {})
        monkeypatch.setattr(bt_session, "_apply_chromium_sandbox_args", lambda _env: None)
        monkeypatch.setattr(bt_session, "_popen_agent_browser", _popen)
        monkeypatch.setattr(
            bt_session,
            "_discard_timed_out_browser_session",
            lambda task, info, socket_dir: discarded.append((task, info, socket_dir)),
        )

        result = bt_session._spawn_and_collect(
            "managed-task", session_info, ["agent-browser"], "open", "auto", 5
        )

        assert result["success"] is False
        assert "_daemon_expected" not in session_info
        assert captured["env"]["AGENT_BROWSER_PROFILE"] == str(tmp_path / "chrome-profile")
        assert discarded == [("managed-task", session_info, str(tmp_path))]

    def test_normal_navigation_error_does_not_discard_local_session(self):
        session_info = {
            "session_name": "h_healthy123",
            "bb_session_id": None,
            "cdp_url": None,
            "features": {"local": True},
        }

        assert bt_session._fatal_local_open_failure(
            "open",
            {"success": False, "error": "net::ERR_NAME_NOT_RESOLVED"},
            session_info,
            "auto",
        ) is False

    def test_cloud_session_does_not_receive_managed_local_profile(self, tmp_path, monkeypatch):
        session_info = {
            "session_name": "cloud-session",
            "bb_session_id": "bb-1",
            "cdp_url": None,
        }
        captured = {}

        class _Proc:
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def _popen(argv, env, socket_dir, tag):
            captured["env"] = dict(env)
            (tmp_path / f"_stdout_{tag}").write_text(
                '{"success":true,"data":{}}', encoding="utf-8"
            )
            (tmp_path / f"_stderr_{tag}").write_text("", encoding="utf-8")
            return _Proc()

        monkeypatch.setattr(bt_session, "_prepare_session_socket_dir", lambda _name: str(tmp_path))
        monkeypatch.setattr(bt_session, "_agent_browser_command_env", lambda _dir: {})
        monkeypatch.setattr(bt_session, "_apply_chromium_sandbox_args", lambda _env: None)
        monkeypatch.setattr(bt_session, "_popen_agent_browser", _popen)

        result = bt_session._spawn_and_collect(
            "cloud-task", session_info, ["agent-browser"], "open", "auto", 5
        )

        assert result["success"] is True
        assert "AGENT_BROWSER_PROFILE" not in captured["env"]

    def test_cloud_open_failure_never_uses_local_fatal_cleanup(self):
        session_info = {
            "session_name": "cloud-session",
            "bb_session_id": "bb-1",
            "cdp_url": "ws://cloud.invalid/devtools/browser/1",
        }

        assert bt_session._fatal_local_open_failure(
            "open",
            {"success": False, "error": "Chrome exited early without DevToolsActivePort"},
            session_info,
            "auto",
        ) is False

    def test_started_local_session_detects_dead_daemon(self, tmp_path, monkeypatch):
        session_name = "h_dead123"
        socket_dir = tmp_path / f"agent-browser-{session_name}"
        socket_dir.mkdir()
        (socket_dir / f"{session_name}.pid").write_text("4242", encoding="utf-8")
        monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
        monkeypatch.setattr(bt_lifecycle, "_pid_exists", lambda pid: False)

        assert bt_session._local_backend_process_dead({
            "session_name": session_name,
            "bb_session_id": None,
            "cdp_url": None,
            "features": {"local": True},
            "_daemon_expected": True,
        }) is True
