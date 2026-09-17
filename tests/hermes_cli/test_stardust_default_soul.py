"""Regression coverage for Stardust-owned default assistant identity."""

from hermes_cli.default_soul import (
    DEFAULT_SOUL_MD,
    _PRE_STARDUST_DEFAULT_SOUL,
    is_legacy_template_soul,
)


def test_default_soul_is_stardust_personal_assistant_contract():
    assert DEFAULT_SOUL_MD.startswith("You are Stardust, a long-lived personal AI assistant")
    assert "answer directly instead of turning it into an action workflow" in DEFAULT_SOUL_MD
    assert "use the available tools or delegate bounded work" in DEFAULT_SOUL_MD
    assert "survive a restart belongs on a durable scheduler or task rail" in DEFAULT_SOUL_MD
    assert "Never let background work steal the user's focus" in DEFAULT_SOUL_MD
    assert "Hermes Agent, built by Nous Research" not in DEFAULT_SOUL_MD


def test_untouched_pre_stardust_default_is_safe_to_migrate():
    assert is_legacy_template_soul(_PRE_STARDUST_DEFAULT_SOUL)
    assert is_legacy_template_soul(_PRE_STARDUST_DEFAULT_SOUL.replace("\u2014", "--"))


def test_customized_pre_stardust_default_is_never_treated_as_template():
    customized = _PRE_STARDUST_DEFAULT_SOUL + " Always answer me in Chinese."
    assert not is_legacy_template_soul(customized)
