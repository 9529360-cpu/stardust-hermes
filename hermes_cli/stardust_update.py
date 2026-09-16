"""Stardust-owned update entrypoint.

The mature Hermes updater remains the implementation engine, but Stardust owns
its product source.  This wrapper is the authority boundary: an in-place update
may run only from ``9529360-cpu/stardust-hermes`` and the legacy fork/upstream
sync path is disabled for the duration of the update.

NousResearch/hermes-agent remains a maintainer reference only.  Importing from
that upstream is a deliberate repository-maintenance operation, never an end
user update operation.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

PRODUCT_REPOSITORY = "9529360-cpu/stardust-hermes"
PRODUCT_GIT_URL = f"https://github.com/{PRODUCT_REPOSITORY}.git"
PRODUCT_REPOSITORY_CANONICAL = f"github.com/{PRODUCT_REPOSITORY}".lower()

logger = logging.getLogger(__name__)


def canonical_github_remote(url: str | None) -> str:
    """Normalize common GitHub remote forms to ``github.com/owner/repo``."""
    if not url:
        return ""
    value = str(url).strip()
    lowered = value.lower()
    if lowered.startswith("git@github.com:"):
        value = "github.com/" + value[len("git@github.com:"):]
    elif lowered.startswith("ssh://git@github.com/"):
        value = "github.com/" + value[len("ssh://git@github.com/"):]
    else:
        try:
            parsed = urlparse(value)
            if parsed.netloc and parsed.path:
                value = f"{parsed.netloc}{parsed.path}"
        except Exception:
            pass
    return value.strip().rstrip("/").removesuffix(".git").lower()


def is_product_remote(url: str | None) -> bool:
    """True only for the Stardust product repository, regardless of SSH/HTTPS form."""
    return canonical_github_remote(url) == PRODUCT_REPOSITORY_CANONICAL


def _git_origin(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _require_product_git_origin(project_root: Path) -> str:
    """Return origin URL or fail closed when the checkout is not Stardust-owned."""
    origin = _git_origin(project_root)
    if is_product_remote(origin):
        return str(origin)

    print("✗ Stardust update refused: this checkout is not attached to the Stardust product repository.")
    print(f"  Expected origin: {PRODUCT_GIT_URL}")
    print(f"  Current origin:  {origin or '(missing/unreadable)'}")
    print("  Stardust never rewrites developer/fork remotes automatically.")
    print("  If this is an installed copy, reinstall from the Stardust-owned installer.")
    raise SystemExit(2)


def _refuse_non_git_update(project_root: Path) -> None:
    """Keep the legacy Nous ZIP fallback out of the Stardust product path."""
    print("✗ This Stardust installation is not a product git checkout, so in-place source update is disabled.")
    print(f"  Checkout: {project_root}")
    print("  Reinstall/repair from the Stardust repository release or bootstrap installer instead.")
    raise SystemExit(2)


@contextmanager
def _stardust_updater_scope():
    """Make the legacy updater treat the already-validated Stardust origin as official.

    The mature updater has a fork helper that otherwise offers to fetch
    ``NousResearch/hermes-agent`` and sync it into ``origin/main``.  Product
    updates must never take that path.  The override is process-local and is
    restored even when the updater exits through ``SystemExit``.
    """
    import hermes_cli.update_cmd as update_cmd

    original_is_fork = update_cmd._is_fork
    update_cmd._is_fork = lambda _origin_url: False
    try:
        yield update_cmd
    finally:
        update_cmd._is_fork = original_is_fork


def _run_mature_update(args, *, update_cmd, main_mod) -> None:
    """Run the existing transactional updater with its lock/receipt/output boundary."""
    gateway_mode = bool(getattr(args, "gateway", False))
    update_io_state = main_mod._install_hangup_protection(gateway_mode=gateway_mode)

    from hermes_cli.update_lock import UPDATE_EXIT_CONCURRENT, UpdateLock, describe_holder

    update_lock = UpdateLock()
    if not update_lock.acquire():
        print(describe_holder(update_lock.holder))
        main_mod._finalize_update_output(update_io_state)
        raise SystemExit(UPDATE_EXIT_CONCURRENT)

    handoff_exit_code: int | None = None
    try:
        update_cmd._cmd_update_impl(args, gateway_mode=gateway_mode)
    except SystemExit as update_exit:
        code = update_exit.code if isinstance(update_exit.code, int) else 1
        main_mod._finalize_update_receipt(code, f"sys.exit({code})")
        handoff_exit_code = update_exit.code if isinstance(update_exit.code, int) else 0
        raise
    except BaseException as update_exc:
        main_mod._finalize_update_receipt(1, f"{type(update_exc).__name__}: {update_exc}")
        raise
    else:
        from hermes_cli.update_receipt import COMMAND_BOUNDARY_STOP_REASON

        main_mod._finalize_update_receipt(0, COMMAND_BOUNDARY_STOP_REASON)
        handoff_exit_code = 0
    finally:
        update_lock.release()
        main_mod._finalize_update_output(update_io_state)

        reexec_env = getattr(main_mod, "_UPDATE_REEXEC_ENV", None)
        if handoff_exit_code is not None and reexec_env and os.environ.get(reexec_env) == "1":
            logger.debug(
                "Stardust update hand-off child %s exiting via os._exit(%s)",
                os.getpid(), handoff_exit_code,
            )
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(handoff_exit_code)


def cmd_update(args):
    """Update Stardust from its own repository, never from Hermes upstream."""
    from hermes_cli import main as main_mod
    from hermes_cli.config import is_managed

    project_root = Path(main_mod.PROJECT_ROOT)

    # Managed/container installs and --plan retain their canonical preflight
    # behavior.  These paths do not perform a product-source fetch.
    if is_managed() or bool(getattr(args, "plan", False)):
        if main_mod._update_preflight_handled(args):
            return

    # Stardust intentionally supports in-place updates only for its own git
    # checkout.  This also prevents the legacy Windows ZIP fallback from ever
    # downloading the NousResearch source archive.
    if not (project_root / ".git").exists():
        if main_mod._update_preflight_handled(args):
            return
        _refuse_non_git_update(project_root)

    _require_product_git_origin(project_root)

    with _stardust_updater_scope() as update_cmd:
        # Includes admission checks and `update --check`.  Running it inside
        # the scope makes explicit checks compare origin/main from Stardust,
        # rather than entering the old fork/upstream synchronization path.
        if main_mod._update_preflight_handled(args):
            return
        _run_mature_update(args, update_cmd=update_cmd, main_mod=main_mod)
