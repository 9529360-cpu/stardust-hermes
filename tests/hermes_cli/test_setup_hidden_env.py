"""Setup forms expose required credentials and hide self-configuring platform knobs."""

import io

import pytest

from hermes_cli.setup_hidden_env import is_setup_hidden_env


class TestIsSetupHiddenEnv:
    @pytest.mark.parametrize(
        "key",
        [
            "TELEGRAM_HOME_CHANNEL",
            "TELEGRAM_HOME_CHANNEL_NAME",
            "TELEGRAM_ALLOW_ALL_USERS",
            "TELEGRAM_REPLY_TO_MODE",
            "WEIXIN_GROUP_REQUIRE_MENTION",
            "WHATSAPP_CLOUD_HOME_CHANNEL_NAME",
            "WHATSAPP_REQUIRE_MENTION",
        ],
    )
    def test_self_configuring_knobs_are_hidden(self, key):
        assert is_setup_hidden_env(key)

    def test_suffix_matching_applies_to_future_platforms(self):
        """A future plugin gets the same presentation policy without an adapter-name list."""
        for key in ("SAMPLECHAT_ALLOW_ALL_USERS", "CUSTOM_PLATFORM_HOME_CHANNEL_NAME"):
            assert is_setup_hidden_env(key), key


class TestChannelCards:
    def test_telegram_card_keeps_required_access_fields_only(self):
        from hermes_cli.web_server_messaging import _build_catalog_entry

        entry = _build_catalog_entry("telegram")
        env_vars = set(entry["env_vars"])
        assert "TELEGRAM_BOT_TOKEN" in env_vars
        assert "TELEGRAM_ALLOWED_USERS" in env_vars
        assert "TELEGRAM_HOME_CHANNEL" not in env_vars
        assert "TELEGRAM_REPLY_TO_MODE" not in env_vars

    def test_no_card_shows_a_hidden_knob(self):
        from hermes_cli.web_server_messaging import _messaging_platform_catalog

        for entry in _messaging_platform_catalog():
            for key in entry["env_vars"]:
                assert not is_setup_hidden_env(key), f"{entry['id']}: {key}"

    def test_hidden_knobs_stay_on_the_keys_page(self):
        """Hidden self-configuring vars stay manually configurable but are not channel-card fields."""
        from hermes_cli.web_server_messaging import _channel_managed_env_keys

        managed = _channel_managed_env_keys()
        assert "TELEGRAM_HOME_CHANNEL" not in managed
        assert "TELEGRAM_REPLY_TO_MODE" not in managed


class TestCliWizard:
    """The standard setup form asks for Telegram's token but not hidden tuning knobs."""

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
        return tmp_path

    def _run(self, answers, monkeypatch):
        from hermes_cli import gateway as gw

        platform = {
            "key": "telegram",
            "label": "Telegram",
            "emoji": "✈️",
            "token_var": "TELEGRAM_BOT_TOKEN",
            "vars": [
                {"name": "TELEGRAM_BOT_TOKEN", "help": "Bot token", "prompt": "Bot token"},
                {"name": "TELEGRAM_HOME_CHANNEL", "help": "Home channel", "prompt": "Home channel"},
                {"name": "TELEGRAM_REPLY_TO_MODE", "help": "Reply mode", "prompt": "Reply mode"},
            ],
        }
        monkeypatch.setattr(gw, "_confirm_reconfigure", lambda *args, **kwargs: True)
        monkeypatch.setattr(gw, "_telegram_auto_setup", lambda token_var: (False, None))
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(answers) + "\n"))
        gw._setup_standard_platform(platform)

    def _env(self, home):
        path = home / ".env"
        if not path.exists():
            return {}
        out = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out

    def test_wizard_stops_asking_about_hidden_knobs(self, home, monkeypatch, capsys):
        self._run(["123456:ABC-test-token"], monkeypatch)

        written = self._env(home)
        assert written["TELEGRAM_BOT_TOKEN"] == "123456:ABC-test-token"
        assert "TELEGRAM_HOME_CHANNEL" not in written
        assert "TELEGRAM_REPLY_TO_MODE" not in written

        out = capsys.readouterr().out
        assert "Home channel" not in out
        assert "Reply mode" not in out

    def test_required_token_still_gates_setup(self, home, monkeypatch):
        self._run([""], monkeypatch)
        assert "TELEGRAM_BOT_TOKEN" not in self._env(home)


class TestStillConfigurable:
    def test_gateway_still_honors_hidden_telegram_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "999")
        monkeypatch.setenv("TELEGRAM_REQUIRE_MENTION", "true")

        from gateway.config import Platform, load_gateway_config

        config = load_gateway_config()
        telegram = config.platforms[Platform.TELEGRAM]
        assert telegram.home_channel is not None
        assert telegram.home_channel.chat_id == "999"
        from hermes_cli.gateway import get_env_value
        assert get_env_value("TELEGRAM_REQUIRE_MENTION") == "true"
