"""Tests for gateway/channel_directory.py — channel resolution and display."""

import asyncio
import json
import os
import threading
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import Platform, RETIRED_PLATFORMS
from gateway.channel_directory import (
    build_channel_directory,
    lookup_channel_type,
    resolve_channel_name,
    format_directory_for_display,
    load_directory,
    _build_from_sessions,
)


import pytest


@pytest.fixture(autouse=True)
def _isolate_channel_aliases(tmp_path_factory):
    """Point the alias overlay at a nonexistent path by default so a real
    ~/.stardust/channel_aliases.json never leaks into directory tests. Tests
    that exercise aliases patch CHANNEL_ALIASES_PATH themselves inside the
    test body, which takes precedence over this outer patch."""
    missing = tmp_path_factory.mktemp("aliases") / "none.json"
    with patch("gateway.channel_directory.CHANNEL_ALIASES_PATH", missing):
        yield


def _write_directory(tmp_path, platforms):
    """Helper to write a fake channel directory."""
    data = {"updated_at": "2026-01-01T00:00:00", "platforms": platforms}
    cache_file = tmp_path / "channel_directory.json"
    cache_file.write_text(json.dumps(data))
    return cache_file


class TestLoadDirectory:
    def test_missing_file(self, tmp_path):
        with patch("gateway.channel_directory.DIRECTORY_PATH", tmp_path / "nope.json"):
            result = load_directory()
        assert result["updated_at"] is None
        assert result["platforms"] == {}


class TestBuildChannelDirectoryWrites:
    def test_failed_write_preserves_previous_cache(self, tmp_path, monkeypatch):
        cache_file = _write_directory(tmp_path, {
            "telegram": [{"id": "123", "name": "Alice", "type": "dm"}]
        })
        previous = json.loads(cache_file.read_text())

        import utils

        def broken_dump(data, fp, *args, **kwargs):
            fp.write('{"updated_at":')
            fp.flush()
            raise OSError("disk full")

        # Fault the canonical writer's serializer (the seam the directory writes through), not
        # json.dump — the helper serializes to a str first, so a stdlib patch never fires.
        monkeypatch.setattr(utils, "_dump_json", broken_dump)

        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file):
            asyncio.run(build_channel_directory({}))
            result = load_directory()

        assert result == previous

    def test_uses_adapter_list_channels_when_available(self, tmp_path):
        class AdapterWithChannels:
            async def list_channels(self):
                return [
                    {"id": "default", "name": "主对话", "type": "dm"},
                    {"id": "family_1", "name": "达拉崩吧", "type": "group"},
                    {"id": "", "name": "ignored", "type": "dm"},
                    {"id": "family_1", "name": "duplicate", "type": "group"},
                ]

        cache_file = tmp_path / "channel_directory.json"
        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file):
            directory = asyncio.run(build_channel_directory({Platform.TELEGRAM: AdapterWithChannels()}))

        assert directory["platforms"]["telegram"] == [
            {"id": "default", "name": "主对话", "type": "dm"},
            {"id": "family_1", "name": "达拉崩吧", "type": "group"},
        ]


class TestBuildChannelDirectoryOffload:
    def test_session_discovery_runs_off_event_loop_thread(self, tmp_path):
        cache_file = tmp_path / "channel_directory.json"
        loop_thread = threading.get_ident()
        builder_threads = []

        def fake_build_from_sessions(_platform):
            builder_threads.append(threading.get_ident())
            return []

        with patch("gateway.channel_directory._build_from_sessions", side_effect=fake_build_from_sessions), \
             patch("gateway.channel_directory.DIRECTORY_PATH", cache_file):
            asyncio.run(build_channel_directory({Platform.WEIXIN: object()}))

        assert builder_threads
        assert all(tid != loop_thread for tid in builder_threads)

    def test_directory_write_runs_off_event_loop_thread(self, tmp_path):
        """The persist step calls os.fsync, which blocks the loop until the write
        reaches stable storage. #60794 moved the builders off the loop; the write
        stayed on it."""
        cache_file = tmp_path / "channel_directory.json"
        loop_thread = threading.get_ident()
        write_threads = []

        def fake_write(path, data, *args, **kwargs):
            write_threads.append(threading.get_ident())

        with patch("gateway.channel_directory.atomic_json_write", side_effect=fake_write), \
             patch("gateway.channel_directory.DIRECTORY_PATH", cache_file):
            asyncio.run(build_channel_directory({}))

        assert write_threads
        assert all(tid != loop_thread for tid in write_threads)


class TestResolveChannelName:
    def _setup(self, tmp_path, platforms):
        cache_file = _write_directory(tmp_path, platforms)
        stack = ExitStack()
        stack.enter_context(patch("gateway.channel_directory.DIRECTORY_PATH", cache_file))
        retired_fronts = set(platforms) & RETIRED_PLATFORMS
        if retired_fronts:
            relay = SimpleNamespace(
                fronts_platform=lambda platform: getattr(platform, "value", platform) in retired_fronts
            )
            runner = SimpleNamespace(adapters={Platform.RELAY: relay})
            stack.enter_context(patch("gateway.run._gateway_runner_ref", lambda: runner))
        return stack

    def test_exact_match(self, tmp_path):
        platforms = {
            "discord": [
                {"id": "111", "name": "bot-home", "guild": "MyServer", "type": "channel"},
                {"id": "222", "name": "general", "guild": "MyServer", "type": "channel"},
            ]
        }
        with self._setup(tmp_path, platforms):
            assert resolve_channel_name("discord", "bot-home") == "111"
            assert resolve_channel_name("discord", "#bot-home") == "111"

    def test_case_insensitive(self, tmp_path):
        platforms = {
            "slack": [{"id": "C01", "name": "Engineering", "type": "channel"}]
        }
        with self._setup(tmp_path, platforms):
            assert resolve_channel_name("slack", "engineering") == "C01"
            assert resolve_channel_name("slack", "ENGINEERING") == "C01"


    def test_prefix_match_unambiguous(self, tmp_path):
        platforms = {
            "slack": [
                {"id": "C01", "name": "engineering-backend", "type": "channel"},
                {"id": "C02", "name": "design-team", "type": "channel"},
            ]
        }
        with self._setup(tmp_path, platforms):
            # "engineering" prefix matches only one channel
            assert resolve_channel_name("slack", "engineering") == "C01"


    def test_no_match_returns_none(self, tmp_path):
        platforms = {
            "telegram": [{"id": "123", "name": "John", "type": "dm"}]
        }
        with self._setup(tmp_path, platforms):
            assert resolve_channel_name("telegram", "nonexistent") is None


class TestBuildFromSessions:
    def _write_sessions(self, tmp_path, sessions_data):
        """Write sessions.json at the path _build_from_sessions expects."""
        sessions_path = tmp_path / "sessions" / "sessions.json"
        sessions_path.parent.mkdir(parents=True)
        sessions_path.write_text(json.dumps(sessions_data))

    def test_builds_from_sessions_json(self, tmp_path):
        self._write_sessions(tmp_path, {
            "session_1": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "12345",
                    "chat_name": "Alice",
                },
                "chat_type": "dm",
            },
            "session_2": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "67890",
                    "user_name": "Bob",
                },
                "chat_type": "group",
            },
            "session_3": {
                "origin": {
                    "platform": "discord",
                    "chat_id": "99999",
                },
            },
        })

        with patch.dict(os.environ, {"STARDUST_HOME": str(tmp_path)}):
            entries = _build_from_sessions("telegram")

        assert len(entries) == 2
        names = {e["name"] for e in entries}
        assert "Alice" in names
        assert "Bob" in names


class TestFormatDirectoryForDisplay:
    def test_empty_directory(self, tmp_path):
        with patch("gateway.channel_directory.DIRECTORY_PATH", tmp_path / "nope.json"):
            result = format_directory_for_display()
        assert "No messaging platforms" in result

    def test_platform_with_no_channels_gets_hint(self):
        """A configured platform with zero discovered channels is shown with
        a hint instead of being hidden entirely."""
        result = format_directory_for_display({
            "simplex": [],
            "telegram": [{"id": "1", "name": "home", "type": "dm"}],
        })
        assert "Simplex:" in result
        assert "no channels discovered yet" in result
        assert "telegram:home" in result

    def test_explicit_platforms_override_disk(self, tmp_path):
        with patch("gateway.channel_directory.DIRECTORY_PATH", tmp_path / "nope.json"):
            result = format_directory_for_display(
                {"irc": [{"id": "#chan", "name": "#chan", "type": "channel"}]}
            )
        assert "irc:#chan" in result


class TestLookupChannelType:
    def _setup(self, tmp_path, platforms):
        cache_file = _write_directory(tmp_path, platforms)
        stack = ExitStack()
        stack.enter_context(patch("gateway.channel_directory.DIRECTORY_PATH", cache_file))
        retired_fronts = set(platforms) & RETIRED_PLATFORMS
        if retired_fronts:
            relay = SimpleNamespace(
                fronts_platform=lambda platform: getattr(platform, "value", platform) in retired_fronts
            )
            runner = SimpleNamespace(adapters={Platform.RELAY: relay})
            stack.enter_context(patch("gateway.run._gateway_runner_ref", lambda: runner))
        return stack

    def test_forum_channel(self, tmp_path):
        platforms = {
            "discord": [
                {"id": "100", "name": "ideas", "guild": "Server1", "type": "forum"},
            ]
        }
        with self._setup(tmp_path, platforms):
            assert lookup_channel_type("discord", "100") == "forum"


    def test_unknown_chat_id_returns_none(self, tmp_path):
        platforms = {
            "discord": [
                {"id": "200", "name": "general", "guild": "Server1", "type": "channel"},
            ]
        }
        with self._setup(tmp_path, platforms):
            assert lookup_channel_type("discord", "999") is None


class TestChannelAliases:
    """The user-maintained alias overlay is retained but scoped to supported/live targets."""

    def _setup_aliases(self, tmp_path, aliases):
        alias_file = tmp_path / "channel_aliases.json"
        alias_file.write_text(json.dumps(aliases))
        return patch("gateway.channel_directory.CHANNEL_ALIASES_PATH", alias_file)

    def test_retired_aliases_are_filtered_but_file_is_preserved(self, tmp_path):
        cache_file = _write_directory(tmp_path, {
            "discord": [{"id": "old-discord", "name": "Old", "type": "channel"}],
            "slack": [{"id": "old-slack", "name": "Old", "type": "channel"}],
        })
        aliases = {"discord": {"D1": "old discord"}, "slack": {"C1": "old slack"}}
        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file), \
             self._setup_aliases(tmp_path, aliases):
            result = load_directory()
            saved_aliases = json.loads((tmp_path / "channel_aliases.json").read_text())

        assert "discord" not in result["platforms"]
        assert "slack" not in result["platforms"]
        assert saved_aliases == aliases

    def test_live_relay_front_keeps_its_retired_platform_alias(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "channel_directory.json"
        aliases = {"discord": {"D1": "old discord"}, "slack": {"C1": "relay slack"}}
        relay = SimpleNamespace(fronts_platform=lambda platform: platform == "slack")
        runner = SimpleNamespace(adapters={Platform.RELAY: relay})
        monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: runner)
        monkeypatch.setattr("gateway.channel_directory._build_from_sessions", lambda _platform: [])

        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file), \
             self._setup_aliases(tmp_path, aliases):
            directory = asyncio.run(build_channel_directory({Platform.RELAY: relay}))
            reloaded = load_directory()

        assert directory["platforms"]["slack"][0]["name"] == "relay slack"
        assert "discord" not in directory["platforms"]
        assert reloaded["platforms"]["slack"][0]["name"] == "relay slack"
        assert "discord" not in reloaded["platforms"]

    def test_alias_injects_undiscovered_group(self, tmp_path):
        """A group named in the alias file but not yet seen in any session is
        still addressable by name (pre-naming before first traffic)."""
        cache_file = _write_directory(tmp_path, {"whatsapp": []})
        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file), \
             self._setup_aliases(tmp_path, {"whatsapp": {"999@g.us": "marketing"}}):
            assert resolve_channel_name("whatsapp", "marketing") == "999@g.us"
            entries = load_directory()["platforms"]["whatsapp"]
            injected = [e for e in entries if e["id"] == "999@g.us"]
            assert injected and injected[0]["type"] == "group"

    def test_alias_persists_through_rebuild(self, tmp_path, monkeypatch):
        """build_channel_directory must bake aliases into the written file so
        they survive the periodic regeneration, not just live reads."""
        cache_file = tmp_path / "channel_directory.json"
        monkeypatch.setattr("gateway.channel_directory._build_from_sessions",
                            lambda plat: [{"id": "120363@g.us", "name": "120363",
                                           "type": "group", "thread_id": None}]
                            if plat == "whatsapp" else [])
        with patch("gateway.channel_directory.DIRECTORY_PATH", cache_file), \
             self._setup_aliases(tmp_path, {"whatsapp": {"120363@g.us": "general"}}):
            asyncio.run(build_channel_directory({}))
            on_disk = json.loads(cache_file.read_text())
        names = [e["name"] for e in on_disk["platforms"]["whatsapp"]
                 if e["id"] == "120363@g.us"]


class TestActiveProfileRelayDirectoryAliases:
    @staticmethod
    def _install_profile_context(monkeypatch, tmp_path, primary_fronts, secondary_fronts):
        from gateway.run import GatewayRunner

        class Relay:
            def __init__(self, fronts):
                self.fronts = set(fronts)

            def fronts_platform(self, platform):
                return str(getattr(platform, "value", platform)).lower() in self.fronts

        primary_relay = Relay(primary_fronts) if primary_fronts is not None else None
        secondary_relay = Relay(secondary_fronts) if secondary_fronts is not None else None
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.adapters = {Platform.RELAY: primary_relay} if primary_relay else {}
        runner._profile_adapters = {
            "secondary": ({Platform.RELAY: secondary_relay} if secondary_relay else {})
        }
        runner._primary_profile_name = "primary"
        runner._profile_failed_platforms = {}
        runner.config = SimpleNamespace(multiplex_profiles=False)
        active = {"name": "secondary"}
        homes = {name: tmp_path / name for name in ("primary", "secondary", "foreign")}
        for home in homes.values():
            home.mkdir()

        monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: runner)
        monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: active["name"])
        monkeypatch.setattr("gateway.channel_directory.get_hermes_home", lambda: homes[active["name"]])
        monkeypatch.setattr("gateway.channel_directory.CHANNEL_ALIASES_PATH", None)
        monkeypatch.setattr("gateway.relay.egress._home_channel_id", lambda _platform: None)
        monkeypatch.setattr("gateway.relay.egress._session_ids", lambda _platform: set())
        return runner, active, homes, primary_relay, secondary_relay

    @staticmethod
    def _write_aliases(home, aliases):
        (home / "channel_aliases.json").write_text(json.dumps(aliases))

    def test_secondary_front_owns_alias_attestation_across_profile_switch(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids, authorize_relay_target

        _runner, active, homes, primary_relay, secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"discord"}, {"slack"}
        )
        self._write_aliases(homes["primary"], {"discord": {"D-P": "primary discord"}})
        self._write_aliases(homes["secondary"], {"slack": {"C-S": "secondary slack"}})

        assert _directory_ids("slack") == {"C-S"}
        assert authorize_relay_target("slack", "C-S", relay_adapter=secondary_relay) is None

        active["name"] = "primary"
        assert _directory_ids("discord") == {"D-P"}
        assert _directory_ids("slack") == set()
        assert authorize_relay_target("discord", "D-P", relay_adapter=primary_relay) is None

    def test_secondary_does_not_borrow_primary_front_or_alias(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids, authorize_relay_target

        _runner, _active, homes, primary_relay, secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"slack"}, {"discord"}
        )
        self._write_aliases(homes["primary"], {"slack": {"C-P": "primary slack"}})
        self._write_aliases(homes["secondary"], {
            "slack": {"C-S": "foreign slack"},
            "discord": {"D-S": "secondary discord"},
        })

        assert _directory_ids("slack") == set()
        assert _directory_ids("discord") == {"D-S"}
        refusal = authorize_relay_target("slack", "C-S", relay_adapter=secondary_relay)
        assert refusal and "refusing" in refusal.lower()
        assert authorize_relay_target("slack", "C-P", relay_adapter=secondary_relay)

    def test_secondary_without_relay_fails_closed_instead_of_using_primary(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids, authorize_relay_target

        _runner, _active, homes, primary_relay, _secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"slack"}, None
        )
        self._write_aliases(homes["primary"], {"slack": {"C-P": "primary slack"}})
        self._write_aliases(homes["secondary"], {"slack": {"C-S": "secondary stale alias"}})

        assert _directory_ids("slack") == set()
        refusal = authorize_relay_target("slack", "C-P", relay_adapter=primary_relay)
        assert refusal and "refusing" in refusal.lower()

    def test_active_profile_query_error_drops_retired_aliases(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids

        runner, active, homes, _primary_relay, _secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"slack"}, {"discord"}
        )
        self._write_aliases(homes["secondary"], {"slack": {"C-S": "must fail closed"}})
        monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: (_ for _ in ()).throw(RuntimeError("scope failed")))

        assert _directory_ids("slack") == set()
        assert runner.adapters[Platform.RELAY].fronts_platform("slack") is True
        assert active["name"] == "secondary"

    def test_unknown_active_profile_cannot_borrow_primary_relay(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids

        _runner, active, homes, _primary_relay, _secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"slack"}, {"discord"}
        )
        self._write_aliases(homes["foreign"], {"slack": {"C-F": "foreign profile"}})
        active["name"] = "foreign"

        assert _directory_ids("slack") == set()

    def test_missing_runner_drops_retired_aliases(self, tmp_path, monkeypatch):
        from gateway.relay.egress import _directory_ids

        _runner, _active, homes, _primary_relay, _secondary_relay = self._install_profile_context(
            monkeypatch, tmp_path, {"slack"}, {"discord"}
        )
        self._write_aliases(homes["secondary"], {"slack": {"C-S": "no runner"}})
        monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)

        assert _directory_ids("slack") == set()

