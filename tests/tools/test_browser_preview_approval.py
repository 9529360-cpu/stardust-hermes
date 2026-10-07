import pytest

from tools.browser_preview_approval import ActionRisk, classify_browser_preview_action


@pytest.mark.parametrize("action", ["elements", "hover", "scroll", "back", "reload"])
def test_preview_read_and_navigation_keep_explicit_happy_path(action):
    decision = classify_browser_preview_action("preview", action)
    assert decision.risk is ActionRisk.SAFE
    assert not decision.requires_approval


@pytest.mark.parametrize("action", ["click", "type", "press", "unknown"])
def test_preview_mutations_fail_closed_as_high_risk(action):
    decision = classify_browser_preview_action("preview", action)
    assert decision.risk is ActionRisk.HIGH
    assert decision.requires_approval
    assert decision.approval_key == f"preview:{action}"


def test_browser_exec_is_explicitly_not_sandboxed_and_requires_approval():
    decision = classify_browser_preview_action("browser", "execute")
    assert decision.risk is ActionRisk.HIGH
    assert "not sandboxed" in decision.reason


def test_unknown_surface_fails_closed():
    assert classify_browser_preview_action("", "click").requires_approval