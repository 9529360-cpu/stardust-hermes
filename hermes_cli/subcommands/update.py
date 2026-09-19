"""``hermes update`` compatibility parser for Stardust's pinned public updater."""

from __future__ import annotations

from typing import Callable


_PINNED_FLAG_HELP = (
    "Compatibility flag retained for existing callers; accepted but has no effect while "
    "Stardust's public CLI updater is pinned off."
)


def build_update_parser(subparsers, *, cmd_update: Callable) -> None:
    """Attach the compatibility ``update`` subcommand to ``subparsers``."""
    update_parser = subparsers.add_parser(
        "update",
        help="Show Stardust's pinned update status",
        description=(
            "Stardust's inherited CLI updater is intentionally pinned off. "
            "This command does not fetch, pull, reinstall, or restart services."
        ),
        epilog=(
            "To refresh an installer-managed Stardust checkout, re-run the Stardust-owned "
            "installer documented in the repository and website."
        ),
    )
    update_parser.add_argument("--gateway", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--check", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--plan", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--no-backup", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--backup", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--yes", "-y", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--keep-stash", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--branch", default=None, metavar="NAME", help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--switch-branch", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--force", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.add_argument("--force-venv", action="store_true", default=False, help=_PINNED_FLAG_HELP)
    update_parser.set_defaults(func=cmd_update)
