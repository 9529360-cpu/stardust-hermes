"""``hermes update`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def _product_update_handler(cmd_update: Callable) -> Callable:
    """Use Stardust's authority wrapper for the real CLI, while preserving injected test handlers.

    ``main.cmd_update`` intentionally remains a compatibility symbol because a large legacy
    test/plugin surface imports it directly. The product parser is the command boundary, so
    only the real handler supplied by ``hermes_cli.main`` is replaced here; parser-builder
    tests and embedders that inject their own handler keep the documented builder contract.
    """
    if (
        getattr(cmd_update, "__module__", "") == "hermes_cli.main"
        and getattr(cmd_update, "__name__", "") == "cmd_update"
    ):
        from hermes_cli.stardust_update import cmd_update as stardust_cmd_update

        return stardust_cmd_update
    return cmd_update


def build_update_parser(subparsers, *, cmd_update: Callable) -> None:
    """Attach the ``update`` subcommand to ``subparsers``."""
    update_parser = subparsers.add_parser(
        "update", help="Update Stardust from the Stardust repository",
        description="Pull reviewed Stardust changes from 9529360-cpu/stardust-hermes and reinstall dependencies")
    update_parser.add_argument(
        "--gateway", action="store_true", default=False,
        help="Gateway mode: use file-based IPC for prompts instead of stdin (used internally by /update)",
    )
    update_parser.add_argument(
        "--check", action="store_true", default=False,
        help="Check the Stardust repository for an update without installing anything")
    update_parser.add_argument(
        "--plan", action="store_true", default=False,
        help="Show the update plan and exit without changing anything: install "
            "kind (git/docker/nix), every running Hermes service across all "
            "profiles with its supervisor and running code version, and how "
            "each will be restarted. Read-only; safe on a live fleet.")
    update_parser.add_argument(
        "--no-backup", action="store_true", default=False,
        help="Skip ALL pre-update backups for this run (both the quick state snapshot and the full zip; overrides updates.pre_update_backup)",
    )
    update_parser.add_argument(
        "--backup", action="store_true", default=False,
        help="Force a FULL pre-update backup (quick state snapshot + HERMES_HOME zip) for this run, regardless of updates.pre_update_backup",
    )
    update_parser.add_argument(
        "--yes", "-y", action="store_true", default=False,
        help="Run without blocking on prompts. Stardust never adds or syncs a NousResearch upstream remote during product update.",
    )
    update_parser.add_argument(
        "--keep-stash", action="store_true", default=False,
        help="Do NOT re-apply local changes after the update. Uncommitted "
            "changes are still stashed so the update can proceed, but they "
            "stay parked in git stash instead of being restored onto the "
            "updated code. Used by the desktop updater so local source edits "
            "never silently ride along across updates.")
    update_parser.add_argument(
        "--branch", default=None, metavar="NAME",
        help="Update against this branch of the Stardust repository instead of the default (main). "
            "If the local checkout is on a different branch, the updater may switch to the requested "
            "branch using its normal safety rules.")
    update_parser.add_argument(
        "--switch-branch", action="store_true", default=False,
        help="With updates.parked_branch_strategy: update_in_place configured, "
            "override it for this run: switch to the update target and update "
            "THERE instead of merging the target into the checked-out branch. "
            "The branch is left exactly as it was — no merge commit is written "
            "into its history. Use on long-lived feature branches where an "
            "update-driven merge commit would pollute the branch. No effect "
            "under the default strategy (switch), which already switches. "
            "Still refuses to touch a dirty tree.")
    update_parser.add_argument(
        "--force", action="store_true", default=False,
        help="Windows: proceed with the update even when another hermes.exe is detected. The concurrent process will likely cause WinError 32 warnings. Does NOT bypass the venv-process guard (see --force-venv).",
    )
    update_parser.add_argument(
        "--force-venv", action="store_true", default=False,
        help="Windows: mutate the venv even while other processes are running from its interpreter (desktop backend, gateway, terminals). Those processes keep native .pyd files locked, so the dependency sync will likely fail partway and strand the install half-updated. Use only if you know the detected holders are false positives.",
    )
    update_parser.set_defaults(func=_product_update_handler(cmd_update))
