"""Stardust must not expose Nous Portal as a built-in account service."""

from __future__ import annotations

import argparse

from hermes_cli import portal_cli


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    portal_cli.add_parser(subparsers)
    return parser


def test_legacy_portal_forms_fail_closed_without_touching_nous(monkeypatch, capsys):
    def unexpected_config():
        raise AssertionError("retired portal command must not inspect account/provider config")

    def unexpected_browser(*_args, **_kwargs):
        raise AssertionError("retired portal command must not open Nous-owned pages")

    monkeypatch.setattr(portal_cli, "load_config", unexpected_config)
    monkeypatch.setattr(portal_cli.webbrowser, "open", unexpected_browser)

    parser = _parser()
    for argv in (
        ["portal"],
        ["portal", "login"],
        ["portal", "info"],
        ["portal", "status"],
        ["portal", "open"],
        ["portal", "tools"],
    ):
        args = parser.parse_args(argv)
        assert args.func is portal_cli.portal_command
        assert args.func(args) == 1
        message = capsys.readouterr().err
        assert "not a built-in Stardust account service" in message
        assert "performs no login" in message


def test_legacy_helpers_are_not_parser_targets():
    parser = _parser()
    for argv in (["portal", "login"], ["portal", "info"], ["portal", "open"], ["portal", "tools"]):
        args = parser.parse_args(argv)
        assert args.func not in {
            portal_cli._cmd_login,
            portal_cli._cmd_status,
            portal_cli._cmd_open,
            portal_cli._cmd_tools,
        }
