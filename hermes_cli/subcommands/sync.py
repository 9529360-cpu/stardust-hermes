"""``stardust sync`` -- the retired upstream Skill Sync entry.

Skill Sync is no longer part of Stardust. The command stays registered (hidden from help) only so
scripts written for it get a clear notice and a non-zero exit instead of an argparse error.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_sync_parser(subparsers, *, cmd_sync: Callable) -> None:
    """Attach the hidden ``sync`` entry; every old argument is accepted and ignored."""
    sync_parser = subparsers.add_parser("sync", help=argparse.SUPPRESS)
    sync_parser.add_argument("legacy_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    sync_parser.set_defaults(func=cmd_sync)
