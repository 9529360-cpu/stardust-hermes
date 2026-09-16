from __future__ import annotations


def test_stardust_defaults_disable_passive_update_checks() -> None:
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["updates"]["check"] is False


def test_passive_update_check_returns_before_network(monkeypatch) -> None:
    import hermes_cli.banner as banner

    # The product default must short-circuit before install detection, git
    # probing, GitHub API calls, or any other update machinery is reached.
    monkeypatch.setattr(
        banner,
        "_resolve_repo_dir",
        lambda: (_ for _ in ()).throw(AssertionError("passive update check reached git/network path")),
    )

    assert banner.check_for_updates(passive=True) is None
