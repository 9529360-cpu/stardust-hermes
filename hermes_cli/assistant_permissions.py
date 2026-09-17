"""First-party permission policy for Stardust personal-assistant execution.

This module classifies tool calls into three product-level postures:

``allow``   - read-only / planning work may run silently.
``notify``  - reversible local or Stardust-internal mutations may run, but belong in the completion report.
``confirm`` - consequential external effects must cross a human approval boundary first.

Desktop turns reuse the existing interactive approval gate. Durable background workers cannot wait on
that process-local gate, so high-risk calls are paused into the task's durable approval handoff instead.
Existing tool-specific safety checks (notably terminal dangerous-command approval) remain authoritative.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

ALLOW = "allow"
NOTIFY = "notify"
CONFIRM = "confirm"


@dataclass(frozen=True)
class PermissionDecision:
    level: str
    reason: str
    rule_key: str = ""


_READ_ACTIONS = frozenset({
    "get", "read", "list", "search", "find", "fetch", "query", "inspect", "preview",
    "status", "show", "describe", "lookup", "check", "history", "details", "info",
})
_EXTERNAL_WRITE_ACTIONS = frozenset({
    "send", "send_message", "reply", "reply_message", "post", "publish", "comment",
    "add_comment", "create", "update", "edit", "upload", "share", "invite", "react",
})
_DESTRUCTIVE_ACTIONS = frozenset({
    "delete", "remove", "destroy", "revoke", "uninstall", "disconnect", "leave", "kick",
    "ban", "archive", "purge", "clear", "reset", "cancel",
})
_FINANCIAL_ACTIONS = frozenset({
    "buy", "purchase", "order", "pay", "transfer", "withdraw", "trade", "sell", "subscribe",
})

# These are mutations of local files, local execution, or Stardust-owned durable state. They should
# not interrupt the user with an approval card, but the assistant should report them when it finishes.
_NOTIFY_TOOLS = frozenset({
    "write_file", "patch", "skill_manage", "memory", "terminal", "process_manage", "execute_code",
    "delegate_task", "cronjob_manage", "desktop_project", "computer_use", "browser_navigate",
    "browser_click", "browser_type", "browser_scroll", "browser_back", "browser_press",
    "browser_dialog", "browser_cdp", "spotify_playback", "spotify_queue", "spotify_library",
    "kanban_create", "kanban_link", "kanban_unblock", "kanban_comment", "kanban_complete",
    "kanban_block", "kanban_request_review", "kanban_request_changes", "kanban_heartbeat",
    "kanban_attach", "kanban_attach_url",
})

# Tool identity alone proves a consequential external effect. Multipurpose tools such as ``discord``
# and ``manage_connections`` are handled below from their action argument.
_CONFIRM_TOOLS = frozenset({
    "ha_call_service", "discord_admin", "send_message", "yb_send_dm", "yb_send_sticker",
    "feishu_drive_reply_comment", "feishu_drive_add_comment", "browser_vault_fill",
    "browser_vault_save_login", "browser_vault_enter_code",
})

_EFFECT_NAME_FRAGMENTS = (
    "send_", "_send_", "reply_", "_reply_", "add_comment", "publish_", "_publish_",
    "delete_", "_delete_", "remove_", "_remove_", "revoke_", "_revoke_",
    "transfer_", "_transfer_", "purchase_", "_purchase_", "pay_", "_pay_",
)


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _action_matches(value: str, families: frozenset[str]) -> bool:
    normalized = _normalize(value)
    return bool(normalized) and (normalized in families or any(normalized.startswith(f"{item}_") for item in families))


def _action(args: Mapping[str, Any]) -> str:
    for key in ("action", "operation", "op", "verb", "method"):
        value = args.get(key)
        if value is not None and str(value).strip():
            return _normalize(value)
    return ""


def _session_identities() -> tuple[str, str]:
    try:
        from gateway.session_context import get_session_env

        return (
            _normalize(get_session_env("HERMES_SESSION_PLATFORM", "")),
            _normalize(get_session_env("HERMES_SESSION_SOURCE", "")),
        )
    except Exception:
        return (
            _normalize(os.environ.get("HERMES_SESSION_PLATFORM", "")),
            _normalize(os.environ.get("HERMES_SESSION_SOURCE", "")),
        )


def personal_assistant_permissions_active() -> bool:
    """Whether the first-party personal-assistant permission policy owns this call."""
    return "desktop" in _session_identities() or bool(os.environ.get("HERMES_KANBAN_TASK"))


def _durable_worker_confirmation_active() -> bool:
    """True only for the dispatcher-owned Kanban worker, never a nested delegate child."""
    if not os.environ.get("HERMES_KANBAN_TASK"):
        return False
    try:
        from agent.delegation_context import (
            is_delegated_child_context,
            is_dispatcher_owned_worker_context,
        )

        return not is_delegated_child_context() and is_dispatcher_owned_worker_context()
    except Exception:
        # A missing lineage probe must not downgrade a durable worker to process-local approval.
        # The durable handoff itself will fail closed if this is not a legitimate task owner.
        return True


def _decision(level: str, reason: str, category: str = "", tool_name: str = "", action: str = "") -> PermissionDecision:
    parts = ["stardust", category, _normalize(tool_name), _normalize(action)]
    rule_key = ":".join(p for p in parts if p) if level == CONFIRM else ""
    return PermissionDecision(level=level, reason=reason, rule_key=rule_key)


def _connector_decision(tool_name: str, action: str) -> PermissionDecision:
    """Dynamic connector tools are external by definition; unknown mutations fail toward confirmation."""
    if _action_matches(action, _READ_ACTIONS):
        return _decision(ALLOW, "Read-only connector operation.")
    category = "connector-write"
    if _action_matches(action, _DESTRUCTIVE_ACTIONS):
        category = "destructive"
    elif _action_matches(action, _FINANCIAL_ACTIONS):
        category = "financial"
    return _decision(
        CONFIRM,
        f"Stardust wants to run an external connector action via {tool_name}; confirm before it changes data outside Stardust.",
        category, tool_name, action or "unknown",
    )


def classify_tool_permission(tool_name: str, args: Optional[Mapping[str, Any]] = None) -> PermissionDecision:
    """Classify one fully-resolved tool call without performing any side effect."""
    raw_name = str(tool_name or "").strip().lower()
    name = _normalize(raw_name)
    values: Mapping[str, Any] = args if isinstance(args, Mapping) else {}
    action = _action(values)

    if raw_name.startswith("connectors__"):
        connector_action = action or raw_name.rsplit("__", 1)[-1]
        return _connector_decision(raw_name, connector_action)

    # Stardust's own task graph is internal durable state. Mutations are visible/reportable, not a
    # reason to interrupt the user with approval every time the coordinator decomposes work.
    if name.startswith("kanban_"):
        if name in {"kanban_show", "kanban_list", "kanban_attachments"}:
            return _decision(ALLOW, "Read-only durable task inspection.")
        return _decision(NOTIFY, "Updates Stardust's durable task state; proceed and report the change.")

    if name == "background_task":
        if action in {"status", "list", "approvals"}:
            return _decision(ALLOW, "Reads durable background-task state only.")
        return _decision(NOTIFY, "Updates Stardust's durable background-task state; proceed and report the change.")

    if name == "manage_connections":
        if action in _READ_ACTIONS or action in {"", "list_connections"}:
            return _decision(ALLOW, "Reads connection/account status only.")
        return _decision(
            CONFIRM,
            "Stardust wants to change an external account connection or authorization; confirm first.",
            "account", name, action or "change",
        )

    if name == "discord":
        if action in _READ_ACTIONS or action in {"search_members", "fetch_messages", "fetch_channel", "list_channels"}:
            return _decision(ALLOW, "Reads Discord data only.")
        if action:
            return _decision(
                CONFIRM,
                f"Stardust wants to perform the external Discord action '{action}'; confirm before publishing or changing remote state.",
                "external-write", name, action,
            )
        return _decision(NOTIFY, "Discord action is unspecified; keep it visible in the completion report.")

    if name == "cronjob_manage":
        if action in _READ_ACTIONS or action in {"list_jobs"}:
            return _decision(ALLOW, "Reads scheduled-task state only.")
        return _decision(NOTIFY, "Changes Stardust's own schedule; proceed and report the schedule change.")

    if name in _CONFIRM_TOOLS:
        category = "physical" if name == "ha_call_service" else "external-write"
        return _decision(
            CONFIRM,
            f"Stardust wants to perform a consequential external action with {tool_name}; confirm first.",
            category, name, action,
        )

    if any(fragment in name for fragment in _EFFECT_NAME_FRAGMENTS):
        return _decision(
            CONFIRM,
            f"Stardust wants to change external state with {tool_name}; confirm first.",
            "external-write", name, action,
        )

    if _action_matches(action, _DESTRUCTIVE_ACTIONS):
        return _decision(
            CONFIRM,
            f"Stardust wants to perform the destructive action '{action}' with {tool_name}; confirm first.",
            "destructive", name, action,
        )
    if _action_matches(action, _FINANCIAL_ACTIONS):
        return _decision(
            CONFIRM,
            f"Stardust wants to perform the financial action '{action}' with {tool_name}; confirm first.",
            "financial", name, action,
        )

    # Known local/Stardust-owned mutation surfaces have explicit execute-then-notify semantics. Keep
    # this before the generic action-family fallback so a local ``create``/``update`` operation does
    # not become an unnecessary approval prompt merely because it shares a verb with a remote API.
    if name in _NOTIFY_TOOLS:
        return _decision(NOTIFY, "Changes local or Stardust-owned state; proceed and report the result.")

    # Extensible tools/plugins may have innocuous names (``calendar``, ``crm``) while exposing remote
    # write verbs through their arguments. Unknown reads continue to allow, but unknown write families
    # fail toward confirmation so adding a new tool cannot silently punch through the assistant policy.
    if _action_matches(action, _EXTERNAL_WRITE_ACTIONS):
        return _decision(
            CONFIRM,
            f"Stardust wants to perform the write action '{action}' with {tool_name}; confirm before changing external state.",
            "external-write", name, action,
        )

    return _decision(ALLOW, "No consequential side effect is identified by the first-party policy.")


def pre_tool_call_directive(tool_name: str, args: Optional[Mapping[str, Any]] = None) -> Optional[dict[str, str]]:
    """Resolve the first-party permission decision into an execution directive."""
    if not personal_assistant_permissions_active():
        return None
    decision = classify_tool_permission(tool_name, args)
    if decision.level != CONFIRM:
        return None

    if _durable_worker_confirmation_active():
        try:
            from tools.background_task_approval import authorize_or_block_current_worker

            result = authorize_or_block_current_worker(
                tool_name,
                args,
                reason=decision.reason,
                rule_key=decision.rule_key,
            )
        except Exception:
            return {
                "action": "block",
                "message": (
                    "BLOCKED: Stardust could not persist the required background-task approval. "
                    "The action was not executed; do not retry through another route."
                ),
            }
        if result.allowed:
            return None
        return {"action": "block", "message": result.message}

    return {"action": "approve", "message": decision.reason, "rule_key": decision.rule_key}
