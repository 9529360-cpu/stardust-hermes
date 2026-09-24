"""Regression tests for xAI provider label disambiguation."""

from hermes_cli.models import provider_label
from hermes_cli.providers import get_label


def test_xai_oauth_provider_label_is_not_collapsed_to_api_key_label():
    """The model picker must distinguish xAI API-key and OAuth providers."""
    assert get_label("xai") == "xAI"
    assert get_label("xai-oauth") == "xAI Grok OAuth (SuperGrok / Premium+)"
    assert get_label("grok-oauth") == "xAI Grok OAuth (SuperGrok / Premium+)"


def test_xai_api_key_label_does_not_depend_on_models_dev_casing(monkeypatch):
    """External catalog metadata cannot relabel the canonical xAI brand."""
    import agent.models_dev as models_dev

    class LowercaseXai:
        name = "xai"
        env = ()
        api = "https://api.x.ai/v1"
        doc = ""

    monkeypatch.setattr(models_dev, "get_provider_info", lambda _provider: LowercaseXai())

    assert get_label("xai") == "xAI"


