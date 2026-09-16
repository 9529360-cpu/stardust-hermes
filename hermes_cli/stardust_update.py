"""Stardust-owned update entrypoint.

The mature Hermes updater remains the implementation engine, but Stardust owns
its product source. This module is the authority boundary: product update
checks and in-place updates target only ``9529360-cpu/stardust-hermes``.

NousResearch/hermes-agent remains a maintainer reference only. A legacy install
whose ``origin`` is exactly the historical Hermes repository may be migrated to
the Stardust origin when an explicit update is applied; arbitrary developer
forks are never rewritten.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

PRODUCT_REPOSITORY = "9529360-cpu/stardust-hermes"
PRODUCT_GIT_URL = f"https://github.com/{PRODUCT_REPOSITORY}.git"
PRODUCT_REPOSITORY_CANONICAL = f"github.com/{PRODUCT_REPOSITORY}".lower()
LEGACY_UPSTREAM_CANONICAL = "github.com/nousresearch/hermes-agent"
UPDATE_AVAILABLE_NO_COUNT = -1

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


def is_legacy_upstream_remote(url: str | None) -> bool:
    """True only for the historical NousResearch product origin."""
    return canonical_github_remote(url) == LEGACY_UPSTREAM_CANONICAL


def _git_run(project_root: Path, args: list[str], *, timeout: int = 15):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except Exception:
        return None


def _git_origin(project_root: Path) -> str | None:
    result = _git_run(project_root, ["remote", "get-url", "origin"])
    if result is None or result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _print_wrong_origin(origin: str | None) -> None:
    print("✗ Stardust update refused: this checkout is not attached to the Stardust product repository.")
    print(f"  Expected origin: {PRODUCT_GIT_URL}")
    print(f"  Current origin:  {origin or '(missing/unreadable)'}")
    print("  Stardust never rewrites arbitrary developer/fork remotes automatically.")


def _require_product_or_legacy_origin(project_root: Path, *, migrate_legacy: bool) -> str:
    """Validate product authority; optionally migrate the exact historical Hermes origin.

    Read-only checks pass ``migrate_legacy=False`` so ``hermes update --check`` never mutates
    git metadata. The apply path may migrate only the exact NousResearch/hermes-agent origin.
    Missing origins and third-party forks fail closed.
    """
    origin = _git_origin(project_root)
    if is_product_remote(origin):
        return str(origin)
    if is_legacy_upstream_remote(origin):
        if not migrate_legacy:
            return str(origin)
        result = _git_run(project_root, ["remote", "set-url", "origin", PRODUCT_GIT_URL])
        if result is None or result.returncode != 0:
            detail = ((result.stderr or result.stdout or "").strip() if result is not None else "")
            print("✗ Could not migrate the legacy Hermes origin to the Stardust repository.")
            if detail:
                print(f"  {detail.splitlines()[0]}")
            raise SystemExit(1)
        migrated = _git_origin(project_root)
        if not is_product_remote(migrated):
            print("✗ Stardust origin migration could not be verified; update aborted.")
            raise SystemExit(1)
        print(f"→ Migrated legacy product origin to {PRODUCT_GIT_URL}")
        return str(migrated)

    _print_wrong_origin(origin)
    print("  If this is an installed copy, reinstall from the Stardust-owned installer.")
    raise SystemExit(2)


def _require_product_git_origin(project_root: Path) -> str:
    """Compatibility helper: require an already-canonical Stardust origin."""
    origin = _git_origin(project_root)
    if is_product_remote(origin):
        return str(origin)
    _print_wrong_origin(origin)
    print("  If this is an installed copy, reinstall from the Stardust-owned installer.")
    raise SystemExit(2)


def _refuse_non_git_update(project_root: Path) -> None:
    """Keep the legacy Nous ZIP fallback out of the Stardust product path."""
    print("✗ This Stardust installation is not a product git checkout, so in-place source update is disabled.")
    print(f"  Checkout: {project_root}")
    print("  Reinstall/repair from the Stardust repository release or bootstrap installer instead.")
    raise SystemExit(2)


def _is_full_sha(value: str | None) -> bool:
    return bool(value) and len(str(value)) == 40 and all(c in "0123456789abcdefABCDEF" for c in str(value))


def _product_github_compare_payload(current_rev: str, target_rev: str) -> dict | None:
    """GitHub compare payload from the Stardust repository only."""
    if not (_is_full_sha(current_rev) and _is_full_sha(target_rev)):
        return None
    if current_rev == target_rev:
        return {"ahead_by": 0, "commits": []}

    from urllib.request import Request, urlopen

    url = (
        f"https://api.github.com/repos/{PRODUCT_REPOSITORY}/compare/"
        f"{current_rev}...{target_rev}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "stardust-update-check",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _product_github_compare_behind(current_rev: str, target_rev: str) -> int | None:
    """Exact behind count for shallow Stardust checkouts using Stardust's GitHub graph."""
    payload = _product_github_compare_payload(current_rev, target_rev)
    ahead = payload.get("ahead_by") if payload else None
    if isinstance(ahead, int) and not isinstance(ahead, bool) and ahead >= 0:
        return ahead
    return None


def _product_branch_tip(branch: str = "main") -> str | None:
    """Resolve a branch tip from Stardust, never from a configured git remote."""
    from urllib.request import Request, urlopen

    safe_branch = quote(str(branch or "main"), safe="")
    request = Request(
        f"https://api.github.com/repos/{PRODUCT_REPOSITORY}/commits/{safe_branch}",
        headers={
            "Accept": "application/vnd.github.sha",
            "User-Agent": "stardust-update-check",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            sha = response.read().decode("utf-8").strip()
        if _is_full_sha(sha):
            return sha
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "ls-remote", PRODUCT_GIT_URL, f"refs/heads/{branch}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    sha = result.stdout.split()[0]
    return sha if _is_full_sha(sha) else None


def stardust_update_status(project_root: Path, *, branch: str = "main") -> dict:
    """Read-only Stardust update status; never probes or fetches a configured ``upstream`` remote."""
    head_result = _git_run(project_root, ["rev-parse", "HEAD"])
    head = (head_result.stdout or "").strip() if head_result is not None and head_result.returncode == 0 else ""
    target = _product_branch_tip(branch)
    if not _is_full_sha(head) or not _is_full_sha(target):
        return {"behind": None, "head": head or None, "target": target, "commits": []}
    if head == target:
        return {"behind": 0, "head": head, "target": target, "commits": []}

    payload = _product_github_compare_payload(head, target)
    ahead = payload.get("ahead_by") if payload else None
    behind = ahead if isinstance(ahead, int) and not isinstance(ahead, bool) and ahead >= 0 else UPDATE_AVAILABLE_NO_COUNT

    rows = []
    for entry in (payload or {}).get("commits", []) if isinstance(payload, dict) else []:
        commit = entry.get("commit") or {}
        when = ((commit.get("committer") or {}).get("date") or "")
        try:
            from datetime import datetime

            at = int(datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()) if when else 0
        except ValueError:
            at = 0
        rows.append({
            "sha": str(entry.get("sha", ""))[:7],
            "summary": str(commit.get("message", "")).split("\n", 1)[0],
            "author": str((commit.get("author") or {}).get("name", "")),
            "at": at,
        })
    rows.reverse()
    return {"behind": behind, "head": head, "target": target, "commits": rows[:20]}


def _print_product_update_status(status: dict, branch: str) -> None:
    behind = status.get("behind")
    if behind == 0:
        print("✓ Already up to date with Stardust.")
        return
    if behind is None:
        print("✗ Could not reach the Stardust update source.")
        raise SystemExit(1)
    if behind == UPDATE_AVAILABLE_NO_COUNT:
        print(f"☤ Stardust update available (behind {PRODUCT_REPOSITORY}:{branch}).")
    else:
        word = "commit" if behind == 1 else "commits"
        print(f"☤ Stardust update available: {behind} {word} behind {PRODUCT_REPOSITORY}:{branch}.")
    print("  Run 'hermes update' to install.")


@contextmanager
def _stardust_updater_scope():
    """Fence legacy updater source decisions onto the already-validated product origin.

    Apply still reuses the mature transactional updater. Its fork helper would otherwise
    offer to synchronize NousResearch into a fork, and its shallow-count fallback points at
    the historical upstream. Both are overridden only for this command and restored even
    when the updater exits via ``SystemExit``.
    """
    import hermes_cli.banner as banner
    import hermes_cli.update_cmd as update_cmd

    original_is_fork = update_cmd._is_fork
    original_compare_behind = banner._github_compare_behind
    update_cmd._is_fork = lambda _origin_url: False
    banner._github_compare_behind = _product_github_compare_behind
    try:
        yield update_cmd
    finally:
        update_cmd._is_fork = original_is_fork
        banner._github_compare_behind = original_compare_behind


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


def _enforce_update_admission(project_root: Path) -> None:
    """Preserve image/package-managed refusal semantics before any product source operation."""
    from hermes_cli.update_contract import evaluate_update_admission, record_refusal_receipt

    refusal = evaluate_update_admission(project_root)
    if refusal is None:
        return
    print(refusal.message)
    record_refusal_receipt(refusal)
    raise SystemExit(2)


def cmd_update(args):
    """Check/apply Stardust updates from its own repository, never from Hermes upstream."""
    from hermes_cli import main as main_mod
    from hermes_cli.config import is_managed

    project_root = Path(main_mod.PROJECT_ROOT)

    # Managed/container installs and --plan retain their canonical preflight behavior.
    # These paths do not perform a product-source fetch.
    if is_managed() or bool(getattr(args, "plan", False)):
        if main_mod._update_preflight_handled(args):
            return

    if not (project_root / ".git").exists():
        _enforce_update_admission(project_root)
        _refuse_non_git_update(project_root)

    _enforce_update_admission(project_root)

    checking = bool(getattr(args, "check", False))
    origin = _require_product_or_legacy_origin(project_root, migrate_legacy=not checking)

    if checking:
        if is_legacy_upstream_remote(origin):
            print(
                "ℹ Legacy Hermes origin detected. Checking Stardust directly without changing git remotes; "
                "an explicit 'hermes update' will migrate origin to the Stardust repository."
            )
        branch = str(getattr(args, "branch", None) or "main")
        _print_product_update_status(stardust_update_status(project_root, branch=branch), branch)
        return

    with _stardust_updater_scope() as update_cmd:
        # Apply only: check/plan/managed cases were already handled above. The mature
        # preflight retains any remaining safety gates, then the transactional updater runs.
        if main_mod._update_preflight_handled(args):
            return
        _run_mature_update(args, update_cmd=update_cmd, main_mod=main_mod)
