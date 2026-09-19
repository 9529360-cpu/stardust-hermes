from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    source = (ROOT / "cli-config.yaml.example").read_text(encoding="utf-8")
    updates = source.split("\nupdates:\n", 1)[1].split("\n\n", 1)[0]
    assert "\n  check: false" in "\n" + updates
    assert "\n  check: true" not in "\n" + updates


def test_public_update_parser_is_truthful_and_keeps_legacy_flags_parseable(capsys) -> None:
    from hermes_cli.main import cmd_update
    from hermes_cli.subcommands.update import build_update_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_update_parser(subparsers, cmd_update=cmd_update)
    update_parser = subparsers.choices["update"]

    help_text = update_parser.format_help()
    assert "pinned off" in help_text
    assert "Pull the latest changes from git" not in help_text

    args = parser.parse_args(
        ["update", "--check", "--plan", "--backup", "--branch", "main", "--yes"]
    )
    args.func(args)
    output = capsys.readouterr().out
    assert "Stardust local edition is pinned" in output


def test_slash_registry_describes_update_as_pinned_status() -> None:
    from hermes_cli.commands import resolve_command

    command = resolve_command("update")
    assert command is not None
    assert "pinned update status" in command.description
    assert "latest version" not in command.description


def test_public_update_docs_point_to_stardust_refresh_path() -> None:
    source = (ROOT / "website/docs/getting-started/updating.md").read_text(encoding="utf-8")

    assert "public CLI updater is currently **pinned off**" in source
    assert "scripts/install-stardust.sh" in source
    assert "scripts/install-stardust.ps1" in source
    assert "Update to the latest version with a single command:" not in source
    assert "This pulls the latest code from `main`" not in source
    assert "NousResearch/hermes-agent/releases" not in source
