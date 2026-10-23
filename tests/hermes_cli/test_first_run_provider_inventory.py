"""First-run setup must require user configuration, not catalog/host availability."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("explicit, expected", [(False, False), (True, True)])
def test_provider_inventory_requires_explicit_selection(tmp_path, monkeypatch, explicit, expected):
    import os
    from hermes_cli import auth, config, main

    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_TOKEN")) or key == "OPENAI_BASE_URL":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "load_config", lambda: {"model": ""})
    monkeypatch.setattr(config, "get_env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(config, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(auth, "PROVIDER_REGISTRY", {"opencode-free": SimpleNamespace(auth_type="api_key", api_key_env_vars=())})
    monkeypatch.setattr(auth, "is_provider_explicitly_configured", lambda provider: explicit)
    monkeypatch.setattr(auth, "get_auth_status", lambda provider: {"logged_in": True})

    assert main._has_any_provider_configured() is expected
