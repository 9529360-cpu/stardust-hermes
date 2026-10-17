"""Verification provenance for opaque *local* execute_code cells.

Python can write via pathlib, extensions, subprocesses, or state left by a prior
cell without calling a Hermes file tool. Do not try to prove arbitrary Python
read-only by inspecting its text. A cell admitted by the existing approval gate
invalidates the active workspace before and after execution, using the existing
ledger. The second invalidation also supersedes verification run inside the cell.

This is not an OS sandbox, a filesystem diff, or remote-workspace tracking.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_VERIFICATION_NOTE = (
    "execute_code may have modified this workspace outside Hermes file tools. "
    "Earlier verification, including checks run inside this cell, is provisional. "
    "Run the final relevant check with terminal after this cell has finished. "
    "Do not repeat this cell merely to obtain verification."
)


def run_with_workspace_verification(
    run: Callable[[], str], *, cwd: str, session_id: str | None,
) -> str:
    """Wrap an already-authorized local cell; preserve its result and authority.

    The caller must finish approval and resolve the session working directory
    first. No-project and disabled-ledger sessions keep their existing behavior.
    Refuse execution if admission cannot invalidate the old proof; if accounting
    fails after execution, retain the output and report that failure explicitly.
    """
    from agent.verification_evidence import mark_workspace_edited

    try:
        state = mark_workspace_edited(session_id=session_id, cwd=cwd, paths=[cwd])
    except Exception:
        logger.warning("execute_code verification admission failed", exc_info=True)
        return json.dumps({
            "status": "error", "output": "", "execution_attempted": False,
            "error": "execute_code did not run: prior verification could not be invalidated.",
            "verification_tracking_failed": True,
        })
    if state is None:
        return run()

    # Freeze the resolved workspace identity: code may change its own cwd and
    # nested calls may use a subdirectory. Do not use either as a new authority.
    root = state["root"]
    tracking_failed = False
    try:
        result = run()
    except Exception:
        logger.warning("execute_code failed after verification admission", exc_info=True)
        result = json.dumps({
            "status": "error", "output": "", "execution_attempted": True,
            "error": "execute_code raised during execution; files may already have changed.",
        })
    finally:
        # Also runs for interruption/SystemExit. A failed or cancelled cell can
        # have written files before it failed; never resurrect its earlier proof.
        try:
            final_state = mark_workspace_edited(session_id=session_id, cwd=root, paths=[root])
            if final_state is None:
                tracking_failed = True
                logger.warning("execute_code lost its verification scope after execution")
        except Exception:
            tracking_failed = True
            logger.warning("execute_code final verification invalidation failed", exc_info=True)

    try:
        payload = json.loads(result)
        if not isinstance(payload, dict):
            raise ValueError("kernel result is not an object")
    except (TypeError, ValueError):
        payload = {
            "status": "error", "output": result,
            "error": "execute_code returned an invalid result; execution may have completed. Do not rerun blindly.",
        }
    # Host-owned metadata, not a claim made by the child or a filesystem diff.
    payload["workspace_mutation"] = {"root": root, "possible": True, "source": "execute_code"}
    payload["verification_note"] = _VERIFICATION_NOTE
    if tracking_failed:
        payload["execution_status"] = payload.get("status")
        payload["status"] = "error"
        payload["verification_tracking_failed"] = True
        prior = payload.get("error")
        error = "Execution returned, but verification tracking failed; do not reuse prior checks or repeat the cell."
        payload["error"] = f"{prior}\n{error}" if prior else error
    return json.dumps(payload, ensure_ascii=False)
