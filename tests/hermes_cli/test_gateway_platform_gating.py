"""The gateway setup picker exposes Stardust's retained messaging channels."""


def test_gateway_picker_keeps_supported_channels_and_hides_retired_adapters():
    import hermes_cli.gateway as gateway_mod

    keys = {platform["key"] for platform in gateway_mod._all_platforms()}

    assert {"telegram", "whatsapp", "weixin"} <= keys
    assert not {"discord", "slack", "matrix", "irc", "mattermost", "signal"} & keys
