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


def test_stardust_dashboard_backend_updates_are_externally_managed() -> None:
    from hermes_cli.web_server_files import _dashboard_local_update_managed_externally

    # Dashboard check/apply must never reactivate the inherited upstream-oriented
    # update machinery while the Stardust CLI updater is intentionally disabled.
    assert _dashboard_local_update_managed_externally() is True


def test_shipped_example_keeps_passive_updates_off() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "cli-config.yaml.example").read_text(encoding="utf-8")
    updates = source.split("\nupdates:\n", 1)[1].split("\n\n", 1)[0]
    assert "\n  check: false" in "\n" + updates
    assert "\n  check: true" not in "\n" + updates
