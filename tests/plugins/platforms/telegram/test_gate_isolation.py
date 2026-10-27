"""Telegram allow/deny gates remain isolated between multiplex profiles."""

import os


class TestTelegramGateIsolation:
    """Telegram mirror (reported by @yournetworkplug-ctrl in #72348)."""

    def test_scoped_gate_env_prefers_profile_scope(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope(
            {"TELEGRAM_ALLOWED_USERS": "222222222"}
        )
        try:
            assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == "222222222"
        finally:
            secret_scope.reset_secret_scope(token)

    def test_scoped_gate_env_authoritative_scope_miss(self, monkeypatch):
        """Under multiplex, a scope WITHOUT the key must not fall through to
        another profile's process-env value."""
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == ""
        finally:
            secret_scope.reset_secret_scope(token)

    def test_scoped_gate_env_single_profile_fallback(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _scoped_gate_env

        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        assert _scoped_gate_env("TELEGRAM_ALLOWED_USERS") == "111111111"

    def test_telegram_yaml_bridge_skipped_for_scoped_profile(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.telegram.adapter import _apply_yaml_config

        monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            extras = _apply_yaml_config(
                {}, {"allowed_chats": ["-100200"], "allow_from": "222222222"},
            )
        finally:
            secret_scope.reset_secret_scope(token)

        # allowed_chats reaches PlatformConfig.extra via the shared-key loop
        # in gateway/config.py (type-preserving); _apply_yaml_config must not
        # write either gate into the process-global env for a scoped profile.
        assert extras is None or "allowed_chats" not in extras
        assert os.getenv("TELEGRAM_ALLOWED_CHATS") is None
        assert os.getenv("TELEGRAM_ALLOWED_USERS") is None
