"""Bridge durable personal-assistant consent to the existing terminal security engine.

This module does not execute commands and does not own an allowlist. It mirrors the approval engine's
pre-prompt decision boundary closely enough to decide whether a dispatcher-owned background worker
must pause for durable user consent. The terminal's own guard still runs afterwards and remains the
final authority for hardline, sudo, user-deny, Tirith, and dangerous-command enforcement.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TerminalApprovalRequirement:
    requires_approval: bool = False
    reason: str = ""
    rule_key: str = ""
    block_message: str = ""
    context_sha256: str = ""


def _terminal_security_context_sha256(plan: Any, *, has_host_access: bool) -> str:
    """Fingerprint the resolved execution target without persisting its raw configuration.

    A durable approval must not migrate from local -> SSH, host A -> host B, one container image/mount
    policy -> another, or one effective cwd -> another. Only this SHA is carried into the outer exact-
    call fingerprint; host paths/SSH details/container configuration never enter the task event ledger.
    """
    config = plan.config if isinstance(getattr(plan, "config", None), Mapping) else {}
    material = {
        "executor_host": socket.gethostname(),
        "env_type": str(getattr(plan, "env_type", "") or ""),
        "image": str(getattr(plan, "image", "") or ""),
        "cwd": str(getattr(plan, "cwd", "") or ""),
        "host_cwd": str(getattr(plan, "host_cwd", "") or ""),
        "has_host_access": bool(has_host_access),
        "ssh": {
            "host": str(config.get("ssh_host") or ""),
            "user": str(config.get("ssh_user") or ""),
            "port": config.get("ssh_port"),
            "key": str(config.get("ssh_key") or ""),
        },
        "container": {
            "persistent": bool(config.get("container_persistent")),
            "docker_mount_cwd_to_workspace": bool(config.get("docker_mount_cwd_to_workspace")),
            "docker_volumes": config.get("docker_volumes") or [],
            "docker_run_as_host_user": bool(config.get("docker_run_as_host_user")),
            "docker_network": bool(config.get("docker_network", True)),
            "docker_extra_args": config.get("docker_extra_args") or [],
            "docker_shared_container_key": str(config.get("docker_shared_container_key") or ""),
            "docker_persist_across_processes": bool(config.get("docker_persist_across_processes")),
            "singularity_image": str(config.get("singularity_image") or ""),
            "modal_image": str(config.get("modal_image") or ""),
            "daytona_image": str(config.get("daytona_image") or ""),
            "vercel_runtime": str(config.get("vercel_runtime") or ""),
        },
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _resolve_terminal_security_context(args: Mapping[str, Any] | None) -> tuple[Any, bool, str]:
    """Use terminal's own planner so task overrides/backend/cwd resolution cannot drift from execution."""
    values = args if isinstance(args, Mapping) else {}
    command = values.get("command")
    from tools.terminal_tool import _docker_has_host_access, _plan_execution

    task_id = str(os.environ.get("HERMES_KANBAN_TASK") or "").strip() or None
    plan = _plan_execution(
        command,
        task_id=task_id,
        timeout=values.get("timeout"),
        background=bool(values.get("background", False)),
        _host_local=False,
    )
    has_host_access = bool(_docker_has_host_access(plan.config))
    return plan, has_host_access, _terminal_security_context_sha256(plan, has_host_access=has_host_access)


def approval_fingerprint_args(
    args: Mapping[str, Any] | None,
    requirement: TerminalApprovalRequirement | None = None,
) -> dict[str, Any]:
    """Opaque payload for the durable exact-call hash; never forwarded to the terminal handler."""
    context_sha256 = str(requirement.context_sha256 or "") if requirement is not None else ""
    if not context_sha256:
        _plan, _has_host_access, context_sha256 = _resolve_terminal_security_context(args)
    return {
        "tool_args": dict(args) if isinstance(args, Mapping) else {},
        "terminal_security_context_sha256": context_sha256,
    }


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

        plan, has_host_access, context_sha256 = _resolve_terminal_security_context(values)
        env_type = str(plan.env_type or "local")
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

    # The durable task ledger fingerprints the resolved terminal args plus context_sha256, so this
    # descriptive rule key never grants a broad command pattern or session-level allowlist entry.
    return TerminalApprovalRequirement(
        requires_approval=True,
        reason=(
            "Stardust background work wants to run a terminal command flagged by the existing security checks: "
            + "; ".join(dict.fromkeys(warnings))
            + ". Confirm this exact terminal call before it runs."
        ),
        rule_key="stardust:terminal-risk",
        context_sha256=context_sha256,
    )
