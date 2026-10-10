"""Raw CDP reaches the same page data as browser_console, so it follows the same sensitive-data policy."""

import json

import pytest

from tools import browser_cdp_tool as cdp


@pytest.fixture
def implicit_policy(monkeypatch):
    """A cloud or real-profile session: the implicit sensitive-data policy is on and the operator set nothing."""
    monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag", lambda key: False)
    monkeypatch.setattr("tools.browser_tool_eval_policy._restrict_browser_evaluate", lambda: True)
    monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: "")  # reached only once the policy allows the call


def _record_approvals(monkeypatch, decision):
    asked = []

    def fake(tool_name, reason, **kwargs):
        asked.append(kwargs.get("rule_key"))
        return decision

    monkeypatch.setattr("tools.approval.request_tool_approval", fake)
    return asked


class TestSensitiveCdpPolicy:
    def test_evaluating_cookies_asks_first_and_a_denial_stops_the_call(self, monkeypatch, implicit_policy):
        asked = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: pytest.fail("no CDP call without approval"))
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert "denied by user" in result["error"]
        assert asked == ["browser_cdp_sensitive:Runtime.evaluate"]

    def test_cookie_readers_need_approval_even_without_javascript(self, monkeypatch, implicit_policy):
        asked = _record_approvals(monkeypatch, {"approved": False, "message": "denied by user"})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: pytest.fail("no CDP call without approval"))
        result = json.loads(cdp.browser_cdp("Network.getAllCookies"))
        assert "denied by user" in result["error"]
        assert asked == ["browser_cdp_sensitive:Network.getAllCookies"]

    def test_an_approved_call_continues_to_the_endpoint(self, monkeypatch, implicit_policy):
        asked = _record_approvals(monkeypatch, {"approved": True})
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert asked == ["browser_cdp_sensitive:Runtime.evaluate"]
        assert "No CDP endpoint" in result["error"]

    def test_explicit_restrict_evaluate_is_never_approvable(self, monkeypatch, implicit_policy):
        monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag",
                            lambda key: key == "restrict_evaluate")
        asked = _record_approvals(monkeypatch, {"approved": True})
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint",
                            lambda: pytest.fail("no CDP call under an explicit restriction"))
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert "browser.restrict_evaluate" in result["error"]
        assert asked == []

    def test_local_sidecars_keep_the_compatibility_behavior(self, monkeypatch):
        monkeypatch.setattr("tools.browser_tool_eval_policy._browser_eval_flag", lambda key: False)
        monkeypatch.setattr("tools.browser_tool_eval_policy._restrict_browser_evaluate", lambda: False)
        monkeypatch.setattr(cdp, "_resolve_cdp_endpoint", lambda: "")
        asked = _record_approvals(monkeypatch, {"approved": False, "message": "must not be asked"})
        result = json.loads(cdp.browser_cdp("Runtime.evaluate", {"expression": "document.cookie"}))
        assert asked == []
        assert "No CDP endpoint" in result["error"]

    def test_ordinary_methods_are_not_gated(self, monkeypatch, implicit_policy):
        asked = _record_approvals(monkeypatch, {"approved": False, "message": "must not be asked"})
        result = json.loads(cdp.browser_cdp("Target.getTargets"))
        assert asked == []
        assert "No CDP endpoint" in result["error"]
