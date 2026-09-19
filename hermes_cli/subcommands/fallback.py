"""``hermes fallback`` subcommand parser."""

from __future__ import annotations


def build_fallback_parser(subparsers) -> None:
    """Attach the ``fallback`` subcommand to ``subparsers``."""
    from hermes_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback", help="Manage fallback providers and persistent route health",
        description="Manage the fallback provider chain and inspect the profile-scoped persistent "
            "route circuit state. Fallback providers are tried in order when the primary model "
            "fails with rate-limit, overload, or connection errors.")
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list", aliases=["ls"], help="Show the current fallback chain (default when no subcommand)")
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `hermes model`) and append to the chain")
    fallback_subparsers.add_parser(
        "remove", aliases=["rm"], help="Pick an entry to delete from the chain")
    fallback_subparsers.add_parser("clear", help="Remove all fallback entries")
    fallback_subparsers.add_parser(
        "health", help="Show persistent route circuit state without probing providers")
    reset_health = fallback_subparsers.add_parser(
        "reset-health",
        help="Clear persisted route circuit history without changing providers or credentials")
    reset_health.add_argument(
        "-y", "--yes", action="store_true", help="Clear without an interactive confirmation")
    fallback_parser.set_defaults(func=cmd_fallback)
