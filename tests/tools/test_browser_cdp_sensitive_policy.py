"""Raw CDP can reach the same page data as browser_console, and much more. The sensitive-data policy is therefore
default-deny in a restricted session: only methods that return no page data and run no page JavaScript pass."""

import json

import pytest

from tools import browser_cdp_tool as cdp
from tools import browser_tool_cloud as cloud
from tools.registry import registry


@pytest.fixture
def implicit_policy(monkeypatch):
    """A cloud, attached, or real-profile session: the implicit sensitive-data policy is on and the operator set nothing."""
    monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag", lambda key: False)
    monkeypatch.setattr("tools.browser_tool_eval_policy._restrict_browser_evaluate", lambda: True)
    monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: "")  # reached only once the policy allows the call


def _record_approvals(monkeypatch, decision):
    asked = []
    reasons = []

    def fake(tool_name, reason, **kwargs):
        asked.append(kwargs.get("rule_key"))
        reasons.append(reason)
        return decision

    monkeypatch.setattr("tools.approval.request_tool_approval", fake)
    return asked, reasons


class TestSensitiveCdpPolicy:
    def test_evaluating_cookies_asks_first_and_a_denial_stops_the_call(self, monkeypatch, implicit_policy):
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: pytest.fail("no CDP call without approval"))
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert "denied by user" in result["error"]
        assert asked == ["browser_cdp_sensitive:Runtime.evaluate"]

    def test_cookie_readers_need_approval_even_without_javascript(self, monkeypatch, implicit_policy):
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: pytest.fail("no CDP call without approval"))
        result = json.loads(cdp.browser_cdp("Network.getAllCookies"))
        assert "denied by user" in result["error"]
        assert asked == ["browser_cdp_sensitive:Network.getAllCookies"]

    @pytest.mark.parametrize("method", [
        "CacheStorage.requestCachedResponse",  # an authenticated cached response body
        "Page.captureScreenshot",              # page pixels
        "DOM.getOuterHTML",                    # page markup, including form values
        "Accessibility.getFullAXTree",         # accessible text, including input values
        "Debugger.evaluateOnCallFrame",        # runs JavaScript in a paused frame
    ])
    def test_unlisted_sensitive_methods_are_default_denied(self, monkeypatch, implicit_policy, method):
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: pytest.fail("no CDP call without approval"))
        result = json.loads(cdp.browser_cdp(method, {}))
        assert "denied by user" in result["error"]
        assert asked == [f"browser_cdp_sensitive:{method}"]

    @pytest.mark.parametrize("method", ["Page.navigate", "Page.reload", "Target.attachToTarget", "Browser.getVersion"])
    def test_methods_that_return_no_page_data_run_without_a_prompt(self, monkeypatch, implicit_policy, method):
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "must not be asked"})
        result = json.loads(cdp.browser_cdp(method, {}))
        assert asked == []
        assert "No CDP endpoint" in result["error"]

    def test_an_approved_call_continues_to_the_endpoint(self, monkeypatch, implicit_policy):
        asked, _ = _record_approvals(monkeypatch, {"approved": True})
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert asked == ["browser_cdp_sensitive:Runtime.evaluate"]
        assert "No CDP endpoint" in result["error"]

    def test_implicit_prompt_does_not_claim_the_setting_is_enabled(self, monkeypatch, implicit_policy):
        _, reasons = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        cdp.browser_cdp("Page.captureScreenshot", {})
        reason = reasons[0]
        assert reason.startswith("Needs approval")
        assert "is enabled" not in reason
        assert "restrict_evaluate: false" not in reason

    def test_explicit_restrict_evaluate_is_never_approvable(self, monkeypatch, implicit_policy):
        monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag",
                            lambda key: key == "restrict_evaluate")
        asked, _ = _record_approvals(monkeypatch, {"approved": True})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint",
                            lambda: pytest.fail("no CDP call under an explicit restriction"))
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert "browser.restrict_evaluate" in result["error"]
        assert asked == []

    def test_explicit_restriction_refuses_unlisted_methods_outright(self, monkeypatch, implicit_policy):
        monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag",
                            lambda key: key == "restrict_evaluate")
        asked, _ = _record_approvals(monkeypatch, {"approved": True})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint",
                            lambda: pytest.fail("no CDP call under an explicit restriction"))
        result = json.loads(cdp.browser_cdp("CacheStorage.requestCachedResponse", {}))
        assert "browser.restrict_evaluate" in result["error"]
        assert asked == []

    def test_local_sidecars_keep_the_compatibility_behavior(self, monkeypatch):
        monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag", lambda key: False)
        monkeypatch.setattr("tools.browser_tool_eval_policy._restrict_browser_evaluate", lambda: False)
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: "")
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "must not be asked"})
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert asked == []
        assert "No CDP endpoint" in result["error"]


class TestRegisteredHandlerGate:
    """The registry entry is what the model calls, and it can route to an extension controller. The gate has to
    run before routing, or an extension-controlled browser would reach sensitive methods without a prompt."""

    @pytest.fixture
    def routes(self, monkeypatch):
        calls = []

        def spy(action, args, *, fallback, task_id=None, session_id=None, tool_call_id=None):
            calls.append(action)
            return fallback()

        monkeypatch.setattr(cdp, "routed_browser_handler", spy)
        monkeypatch.setattr(cdp, "_browser_cdp_call", lambda *a, **k: json.dumps({"success": True, "via": "local"}))
        return calls

    def test_denied_sensitive_call_is_not_routed(self, monkeypatch, routes, implicit_policy):
        _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        handler = registry.get_entry("browser_cdp").handler
        result = json.loads(handler({"method": "Page.captureScreenshot", "params": {}}, task_id="t"))
        assert "denied by user" in result["error"]
        assert routes == []

    def test_approved_sensitive_call_is_routed_once(self, monkeypatch, routes, implicit_policy):
        asked, _ = _record_approvals(monkeypatch, {"approved": True})
        handler = registry.get_entry("browser_cdp").handler
        result = json.loads(handler({"method": "Page.captureScreenshot", "params": {}}, task_id="t"))
        assert result == {"success": True, "via": "local"}
        assert routes == ["browser_cdp"]
        assert asked == ["browser_cdp_sensitive:Page.captureScreenshot"]  # one prompt, not one per layer

    def test_safe_call_routes_without_a_prompt(self, monkeypatch, routes, implicit_policy):
        asked, _ = _record_approvals(monkeypatch, {"approved": False, "message": "must not be asked"})
        handler = registry.get_entry("browser_cdp").handler
        handler({"method": "Target.getTargets", "params": {}}, task_id="t")
        assert asked == []
        assert routes == ["browser_cdp"]


class TestBrowserLocality:
    """The sensitive-data policy asks whether the browser holds authenticated state. The terminal backend answers a
    different question (SSRF trust), so a Docker or SSH terminal must not make a local browser look sensitive."""

    @pytest.fixture
    def local_browser(self, monkeypatch):
        monkeypatch.setattr(cloud, "_get_cloud_provider", lambda: None)
        monkeypatch.setattr(cloud._cdp, "_get_cdp_override_raw", lambda: "")
        monkeypatch.setattr(cloud._origin(), "_is_camofox_mode", lambda: False)

    def test_remote_terminal_does_not_make_a_local_browser_sensitive(self, monkeypatch, local_browser):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        assert cloud._browser_is_local_sidecar() is True
        assert cloud._is_local_backend() is False  # the SSRF boundary still treats this terminal as remote

    def test_cdp_override_is_sensitive_even_with_a_local_terminal(self, monkeypatch, local_browser):
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.setattr(cloud._cdp, "_get_cdp_override_raw", lambda: "ws://attached.example:9222/devtools")
        assert cloud._browser_is_local_sidecar() is False

    def test_cloud_provider_is_sensitive(self, monkeypatch, local_browser):
        monkeypatch.setattr(cloud, "_get_cloud_provider", lambda: object())
        assert cloud._browser_is_local_sidecar() is False
