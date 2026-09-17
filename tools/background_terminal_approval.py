"""Bridge durable personal-assistant consent to the existing terminal security engine.

This module does not execute commands and does not own an allowlist. It mirrors the approval engine's
pre-prompt decision boundary closely enough to decide whether a dispatcher-owned background worker
must pause for durable user consent. The terminal's own guard still runs afterwards and remains the
final authority for hardline, sudo, user-deny, Tirith, and dangerous-command enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TerminalApprovalRequirement:
    requires_approval: bool = False
    reason: str = ""
    rule_key: str = ""
    block_message: str = ""


def inspect_terminal_approval(args: Mapping[str, Any] | None) -> TerminalApprovalRequirement:
    """Classify one resolved terminal call before it reaches the unattended terminal guard.

    Recoverable dangerous/Tirith findings become one durable user-confirmation request. Unconditional
    floors are returned as hard blocks instead of being made user-approvable. Isolated containers and
    already-bypassed/allowlisted commands keep the terminal engine's existing behavior.
    """
    values = args if isinstance(args, Mapping) else {}
    command = values.get("command")
    if not isinstance(command, str) or not command.strip():
        return TerminalApprovalRequirement()

    try:
        from tools import approval
        from tools import approval_context
        from tools.terminal_tool import _docker_has_host_access, _get_env_config

        config = _get_env_config()
        env_type = str(config.get("env_type") or "local")
        has_host_access = bool(_docker_has_host_access(config))
    except Exception:
        return TerminalApprovalRequirement(
            block_message=(
                "BLOCKED: Stardust could not resolve the terminal security context for this background task. "
                "The command was not executed."
            )
        )

    # Match terminal approval ordering. Isolated sandboxes still enforce user deny rules inside the
    # terminal engine, but do not need a separate durable dangerous-command confirmation.
    if approval._should_skip_container_guards(env_type, has_host_access=has_host_access):
        return TerminalApprovalRequirement()

    floor = approval._floor_block(command, sudo_guard=True)
    if floor is not None:
        return TerminalApprovalRequirement(
            block_message=str(floor.get("message") or "BLOCKED by terminal safety policy.")
        )

    approval_mode = approval_context._get_approval_mode()
    if (
        approval._yolo_active()
        or approval_mode == "off"
        or approval._command_matches_permanent_allowlist(command)
    ):
        return TerminalApprovalRequirement()

    warnings: list[str] = []
    session_key = approval.get_current_session_key()

    tirith_result = approval._tirith_scan(command)
    if tirith_result.get("action") in {"block", "warn"}:
        findings = tirith_result.get("findings") or []
        rule_id = findings[0].get("rule_id", "unknown") if findings else "unknown"
        tirith_key = f"tirith:{rule_id}"
        if not approval.is_approved(session_key, tirith_key):
            warnings.append(approval._format_tirith_description(tirith_result))

    is_dangerous, pattern_key, description = approval.detect_dangerous_command(command)
    if is_dangerous and not approval.is_approved(session_key, pattern_key):
        warnings.append(str(description or "dangerous terminal command"))

    if not warnings:
        return TerminalApprovalRequirement()

    # The durable task ledger fingerprints the complete resolved terminal args, so this rule key is
    # descriptive only; it does not grant a broad command pattern or session-level allowlist entry.
    return TerminalApprovalRequirement(
        requires_approval=True,
        reason=(
            "Stardust background work wants to run a terminal command flagged by the existing security checks: "
            + "; ".join(dict.fromkeys(warnings))
            + ". Confirm this exact terminal call before it runs."
        ),
        rule_key="stardust:terminal-risk",
    )
