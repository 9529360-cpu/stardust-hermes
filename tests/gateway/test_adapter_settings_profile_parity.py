"""Standalone-vs-served parity for adapter SETTINGS (not credentials) under ``gateway.multiplex_profiles``.

A served secondary profile's adapter is constructed inside ``_profile_runtime_scope`` while ``os.environ``
still holds the DEFAULT profile's ``.env``. Every non-credential setting an adapter reads with a bare
``os.getenv`` (ports, hosts, mention gating, reactions, thread policy, notification targets) therefore
resolves to the default profile's value — the adapter behaves differently served than it does standalone.

The invariant: with the SAME profile ``.env`` the adapter resolves the SAME setting whether the profile is
the ambient process home (standalone) or a scoped secondary (served). Each row names the setting and the
callable that resolves it from a constructed adapter, so a regression is reported by setting name.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from agent import secret_scope as ss
from gateway.config import PlatformConfig


@pytest.fixture(autouse=True)
def _multiplex_off_after():
    ss.set_multiplex_active(False)
    yield
    ss.set_multiplex_active(False)


def _served(monkeypatch, default_env: dict, secondary_env: dict, build):
    """``(standalone_value, served_value)`` for one setting: standalone = the secondary's env IS the
    process env; served = default's env in the process, secondary's only in the scope."""
    for k, v in secondary_env.items():
        monkeypatch.setenv(k, v)
    standalone = build()
    for k in secondary_env:
        monkeypatch.delenv(k, raising=False)
    for k, v in default_env.items():
        monkeypatch.setenv(k, v)
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope(dict(secondary_env))
    try:
        served = build()
    finally:
        ss.reset_secret_scope(token)
    return standalone, served


# (module, env var, default-profile value, secondary value, resolver(module) -> value)
_SETTINGS = [
    ("gateway.platforms.weixin", "WEIXIN_BASE_URL", "https://default.invalid", "https://secondary.invalid",
     lambda m: m._wx_secret("WEIXIN_BASE_URL", "")),
    ("gateway.platforms.whatsapp_cloud", "WHATSAPP_CLOUD_WEBHOOK_PORT", "18010", "18110",
     lambda m: m._get_wsecret("WHATSAPP_CLOUD_WEBHOOK_PORT", default="")),
]



@pytest.mark.parametrize(("module", "var", "default_value", "secondary_value", "resolve"), _SETTINGS,
                         ids=[f"{m.rsplit('.', 2)[-2]}:{v}" for m, v, *_ in _SETTINGS])
def test_served_secondary_resolves_its_own_setting(monkeypatch, module, var, default_value, secondary_value, resolve):
    mod = importlib.import_module(module)
    for name in (var, "TELEGRAM_BOT_TOKEN", "WHATSAPP_ENABLED", "WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID",
                 "WHATSAPP_CLOUD_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    creds = {
        "TELEGRAM_BOT_TOKEN": "123:token", "WHATSAPP_ENABLED": "true",
        "WEIXIN_TOKEN": "wx-token", "WEIXIN_ACCOUNT_ID": "acct-1",
        "WHATSAPP_CLOUD_ACCESS_TOKEN": "cloud-token",
    }
    standalone, served = _served(monkeypatch, {var: default_value, **creds}, {var: secondary_value, **creds},
                                 lambda: resolve(mod))
    assert served == standalone, f"{var}: served={served!r} standalone={standalone!r}"
