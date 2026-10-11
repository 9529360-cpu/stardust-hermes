"""The primary profile's handles, checked through every backend resolver.

The primary profile ('default') presents as @stardust. The legacy @hermes stays accepted
wherever a Bot Mode target or mention is resolved, so the model may type either name in an
agent-to-agent handoff and it still lands on the primary. Plain asserts and no fixtures, so
these run under pytest and under a bare interpreter.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gateway import hosted_room_discussion as discussion
from hermes_cli.profiles import validate_profile_name
from tools import bot_mode_dm, bot_mode_probe, bot_relay
from tui_gateway import methods_complete


def test_primary_handle_is_stardust_and_hermes_stays_an_alias():
    assert bot_mode_probe.PRIMARY_HANDLE == "stardust"
    assert "stardust" in bot_mode_probe.PRIMARY_HANDLE_ALIASES
    assert "hermes" in bot_mode_probe.PRIMARY_HANDLE_ALIASES


def test_probe_presents_the_primary_as_stardust_and_teammates_unchanged():
    assert bot_mode_probe._handle("default") == "stardust"
    assert bot_mode_probe._handle("ops") == "ops"


def test_local_dm_target_resolves_both_primary_names_to_the_default_profile():
    roster = ["default", "researcher"]
    assert bot_mode_dm._resolve_local_name("stardust", roster) == "default"
    assert bot_mode_dm._resolve_local_name("Stardust", roster) == "default"
    assert bot_mode_dm._resolve_local_name("hermes", roster) == "default"
    assert bot_mode_dm._resolve_local_name("researcher", roster) == "researcher"
    assert bot_mode_dm._resolve_local_name("nosuchbot", roster) is None
    # An install without a primary profile has nothing for either name to reach.
    assert bot_mode_dm._resolve_local_name("stardust", ["researcher"]) is None


def test_remote_target_matches_the_primary_by_either_name_whatever_handle_the_row_carries():
    # The Desktop still sends the legacy handle on the wire for the primary row.
    roster = [
        {"profile": "default", "handle": "hermes", "connection_id": "cloud-1", "connection_label": "Cloud"},
        {"profile": "researcher", "handle": "researcher", "connection_id": "ssh-vps", "connection_label": "VPS"},
    ]
    assert bot_relay.resolve_remote_target("stardust", roster)["connection_id"] == "cloud-1"
    assert bot_relay.resolve_remote_target("hermes", roster)["connection_id"] == "cloud-1"
    assert bot_relay.resolve_remote_target("@stardust@cloud-1", roster)["profile"] == "default"
    assert bot_relay.resolve_remote_target("default", roster)["connection_id"] == "cloud-1"
    assert bot_relay.resolve_remote_target("stardust@ssh-vps", roster) is None


def test_remote_row_carrying_the_stardust_handle_matches_and_a_user_profile_yields_to_the_primary():
    roster = [
        {"profile": "default", "handle": "stardust", "connection_id": "cloud-1"},
        {"profile": "stardust", "handle": "stardust", "connection_id": "ssh-vps"},  # legacy user profile
    ]
    assert bot_relay.resolve_remote_target("stardust", roster)["connection_id"] == "cloud-1"


def test_remote_forms_present_the_primary_as_stardust():
    roster = [
        {"profile": "default", "handle": "hermes", "connection_id": "cloud-1"},
        {"profile": "researcher", "handle": "researcher", "connection_id": "ssh-vps"},
    ]
    forms = bot_relay.remote_target_forms(roster)
    assert "stardust" in forms
    assert "hermes" not in forms
    assert "researcher" in forms


def test_roster_rows_without_a_handle_default_to_stardust():
    with tempfile.TemporaryDirectory() as tmp:
        bot_relay.write_remote_roster(Path(tmp), [{"profile": "default", "connection_id": "cloud-1"}])
        (row,) = bot_relay.read_remote_roster(Path(tmp))
    assert row["handle"] == "stardust"


def test_no_user_profile_can_take_either_primary_name():
    for name in ("stardust", "hermes"):
        try:
            validate_profile_name(name)
        except ValueError:
            continue
        raise AssertionError(f"profile name {name!r} must be reserved")
    validate_profile_name("researcher")  # an ordinary name still validates


def _at_completions(prefix, profile_names):
    profiles = [SimpleNamespace(name=name, description="") for name in profile_names]
    with mock.patch("hermes_cli.profiles.list_profiles", return_value=profiles):
        return [item["text"] for item in methods_complete._profile_mention_items(prefix)]


def test_at_completion_offers_the_primary_as_stardust():
    profiles = ["default", "researcher"]
    assert "@stardust" in _at_completions("", profiles)
    assert "@stardust" in _at_completions("st", profiles)
    # Typing the legacy name still finds the primary, under its Stardust handle.
    assert "@stardust" in _at_completions("her", profiles)
    assert _at_completions("res", profiles) == ["@researcher"]


def test_at_completion_does_not_double_offer_when_a_real_profile_claims_the_name():
    assert _at_completions("st", ["default", "stardust"]) == ["@stardust"]


def _member(member_id, profile, handle, *, kind="local"):
    target = {"kind": "local", "profile": profile} if kind == "local" else {
        "kind": "peer", "peer_id": "vera", "installation_id": "vera-box", "profile": profile,
        "capability_digest": "0" * 64}
    return discussion.DiscussionMember(member_id, profile, handle, "", target)


def test_hosted_room_mentions_reach_the_local_primary_by_either_name():
    primary = _member("m-primary", "default", "stardust")
    writer = _member("m-writer", "writer", "writer")
    members = (primary, writer)
    for text in ("@stardust take this", "@hermes take this"):
        assert discussion.resolve_mentions([text], members, default_all=False) == (primary,)
    assert discussion.resolve_mentions(["@writer take this"], members, default_all=False) == (writer,)


def test_hosted_room_legacy_primary_handle_is_still_reached_by_the_new_name():
    primary = _member("m-primary", "default", "hermes")
    assert discussion.resolve_mentions(["@stardust hi"], (primary,), default_all=False) == (primary,)


def test_hosted_room_alias_never_reaches_a_peer_default_or_a_room_without_a_local_primary():
    peer_default = _member("m-peer", "default", "default-vera", kind="peer")
    writer = _member("m-writer", "writer", "writer")
    assert discussion.resolve_mentions(["@stardust hi"], (peer_default, writer), default_all=False) == ()
    assert discussion.resolve_mentions(["@hermes hi"], (writer,), default_all=False) == ()
