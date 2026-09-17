"""Durable approval handoff for personal-assistant background workers.

Gateway/CLI approval queues are process-local, while Kanban workers are one-shot subprocesses.
This module stores only approval metadata in the existing task event ledger and reuses the task's
``blocked(kind=needs_input)`` lifecycle. Raw tool arguments are never persisted: exact-call matching
uses a SHA-256 fingerprint of the resolved tool name and arguments. Terminal fingerprints additionally
bind the resolved execution/security context so consent cannot migrate across hosts/backends/mounts.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

_REQUEST = "assistant_approval_requested"
_GRANTED = "assistant_approval_granted"
_DENIED = "assistant_approval_denied"
_CONSUMED = "assistant_approval_consumed"
_EVENT_KINDS = (_REQUEST, _GRANTED, _DENIED, _CONSUMED)


@dataclass(frozen=True)
class WorkerApprovalResult:
    allowed: bool
    approval_id: str = ""
    message: str = ""


def _canonical_args(args: Mapping[str, Any] | None) -> str:
    values = dict(args) if isinstance(args, Mapping) else {}
    try:
        return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        # Tool inputs originate as JSON. This fallback is defensive and is hashed immediately;
        # the representation itself is never written to the task database.
        return repr(sorted(values.items(), key=lambda item: str(item[0])))


def call_fingerprint(tool_name: str, args: Mapping[str, Any] | None) -> str:
    normalized_tool = str(tool_name or "").strip().lower()
    fingerprint_args: Mapping[str, Any] | None = args
    if normalized_tool == "terminal":
        # Terminal consent is target-sensitive: the same shell text on local, SSH, or a differently
        # mounted container is not the same authorized action. The bridge returns an opaque nested
        # payload containing only the original JSON args plus a SHA of the resolved security context.
        from tools.background_terminal_approval import approval_fingerprint_args

        fingerprint_args = approval_fingerprint_args(args)
    material = f"{normalized_tool}\n{_canonical_args(fingerprint_args)}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def _safe_persisted_reason(reason: Any) -> str:
    """Force-redact durable approval copy before it reaches the task event ledger."""
    from agent.redact import redact_sensitive_text

    return redact_sensitive_text(str(reason or "Approval required"), force=True).strip()[:500]


def _load_events(conn, task_id: str) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in _EVENT_KINDS)
    rows = conn.execute(
        f"SELECT id, kind, payload, created_at FROM task_events "
        f"WHERE task_id = ? AND kind IN ({placeholders}) ORDER BY id ASC",
        (task_id, *_EVENT_KINDS),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        events.append({
            "id": int(row["id"]), "kind": row["kind"], "payload": payload,
            "created_at": int(row["created_at"] or 0),
        })
    return events


def _approval_states(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event["payload"]
        approval_id = str(payload.get("approval_id") or "").strip()
        if not approval_id:
            continue
        if event["kind"] == _REQUEST:
            states[approval_id] = {
                "approval_id": approval_id,
                "tool_name": str(payload.get("tool_name") or ""),
                "rule_key": str(payload.get("rule_key") or ""),
                "args_sha256": str(payload.get("args_sha256") or ""),
                "reason": str(payload.get("reason") or ""),
                "requested_at": event["created_at"],
                "request_event_id": event["id"],
                "state": "pending",
            }
            continue
        state = states.get(approval_id)
        if state is None:
            continue
        if event["kind"] == _GRANTED:
            state["state"] = "approved"
        elif event["kind"] == _DENIED:
            state["state"] = "denied"
            if payload.get("reason"):
                state["decision_reason"] = str(payload["reason"])
        elif event["kind"] == _CONSUMED:
            state["state"] = "consumed"
    return states


def list_approvals(conn, task_id: str, *, pending_only: bool = False) -> list[dict[str, Any]]:
    states = list(_approval_states(_load_events(conn, task_id)).values())
    if pending_only:
        states = [item for item in states if item["state"] == "pending"]
    return states


def _pending_match(states: dict[str, dict[str, Any]], fingerprint: str, tool_name: str) -> dict[str, Any] | None:
    for state in reversed(list(states.values())):
        if (
            state["state"] == "pending"
            and state["args_sha256"] == fingerprint
            and state["tool_name"] == tool_name
        ):
            return state
    return None


def _approved_match(states: dict[str, dict[str, Any]], fingerprint: str, tool_name: str) -> dict[str, Any] | None:
    for state in reversed(list(states.values())):
        if (
            state["state"] == "approved"
            and state["args_sha256"] == fingerprint
            and state["tool_name"] == tool_name
        ):
            return state
    return None


def _append_event(conn, kb, task_id: str, kind: str, payload: dict[str, Any]) -> None:
    with kb.write_txn(conn):
        kb._append_event(conn, task_id, kind, payload)


def authorize_or_block_current_worker(
    tool_name: str,
    args: Mapping[str, Any] | None,
    *,
    reason: str,
    rule_key: str,
) -> WorkerApprovalResult:
    """Atomically consume one exact durable grant or record a deduplicated request and pause."""
    task_id = str(os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    if not task_id:
        raise RuntimeError("durable worker approval requires HERMES_KANBAN_TASK")
    normalized_tool = str(tool_name or "").strip()
    fingerprint = call_fingerprint(normalized_tool, args)

    from tools.kanban_tools import _board

    with _board(None) as (kb, conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            raise RuntimeError(f"unknown background task {task_id}")

        allowed = False
        approval_id = ""
        with kb.write_txn(conn):
            states = _approval_states(_load_events(conn, task_id))
            granted = _approved_match(states, fingerprint, normalized_tool)
            if granted is not None:
                approval_id = granted["approval_id"]
                kb._append_event(conn, task_id, _CONSUMED, {
                    "approval_id": approval_id,
                    "tool_name": normalized_tool,
                    "args_sha256": fingerprint,
                })
                allowed = True
            else:
                pending = _pending_match(states, fingerprint, normalized_tool)
                if pending is None:
                    approval_id = f"apr-{uuid.uuid4().hex[:12]}"
                    safe_reason = _safe_persisted_reason(reason)
                    kb._append_event(conn, task_id, _REQUEST, {
                        "approval_id": approval_id,
                        "tool_name": normalized_tool,
                        "rule_key": str(rule_key or "")[:300],
                        "args_sha256": fingerprint,
                        "reason": safe_reason,
                    })
                else:
                    approval_id = pending["approval_id"]

        if allowed:
            return WorkerApprovalResult(True, approval_id=approval_id)

        task = kb.get_task(conn, task_id)
        if task is not None and task.status in {"running", "ready"}:
            block_reason = (
                f"Waiting for user approval {approval_id} before {normalized_tool}. "
                "The requested action has not executed."
            )
            kb.block_task(
                conn,
                task_id,
                reason=block_reason,
                kind="needs_input",
                expected_run_id=task.current_run_id if task.status == "running" else None,
            )

    return WorkerApprovalResult(
        False,
        approval_id=approval_id,
        message=(
            f"BLOCKED: background task is waiting for user approval {approval_id} before "
            f"{normalized_tool}. The action was not executed. Do not retry or use another route; "
            "the task has been durably paused for a user decision."
        ),
    )


def _select_for_decision(
    states: list[dict[str, Any]], approval_id: str, decision: str
) -> dict[str, Any] | None:
    if approval_id:
        return next((item for item in states if item["approval_id"] == approval_id), None)
    pending = [item for item in states if item["state"] == "pending"]
    if pending:
        return pending[-1]
    if decision == "approve":
        approved = [item for item in states if item["state"] == "approved"]
        return approved[-1] if approved else None
    return None


def decide_task_approval(
    conn,
    kb,
    task_id: str,
    *,
    decision: str,
    approval_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Persist a user decision. Approval is retry-safe; denial leaves the task paused."""
    task = kb.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"unknown background task: {task_id}")
    states = list_approvals(conn, task_id)
    selected = _select_for_decision(states, approval_id, decision)
    if selected is None:
        raise ValueError(
            f"no pending approval{f' {approval_id}' if approval_id else ''} for background task {task_id}"
        )
    approval_id = selected["approval_id"]

    if decision == "approve":
        if selected["state"] == "denied":
            raise ValueError(f"approval {approval_id} was denied; resume the task to request consent again")
        if selected["state"] == "consumed":
            raise ValueError(f"approval {approval_id} was already consumed")
        already_decided = selected["state"] == "approved"
        if not already_decided:
            _append_event(conn, kb, task_id, _GRANTED, {
                "approval_id": approval_id,
                "tool_name": selected["tool_name"],
                "args_sha256": selected["args_sha256"],
            })
        # Grant-first is intentional: if unblocking loses a race, the exact one-time grant remains
        # durable while the task stays blocked; a retry can safely unblock it without losing consent.
        task = kb.get_task(conn, task_id)
        unblocked = kb.unblock_task(conn, task_id) if task is not None and task.status == "blocked" else False
        return {
            "ok": True, "task_id": task_id, "approval_id": approval_id,
            "decision": "approved", "already_decided": already_decided,
            "resumed": bool(unblocked), "status": kb.get_task(conn, task_id).status,
        }

    if decision == "deny":
        if selected["state"] != "pending":
            raise ValueError(f"approval {approval_id} is already {selected['state']}")
        _append_event(conn, kb, task_id, _DENIED, {
            "approval_id": approval_id,
            "tool_name": selected["tool_name"],
            "args_sha256": selected["args_sha256"],
            "reason": str(reason or "").strip()[:500],
        })
        if reason and str(reason).strip():
            kb.add_comment(conn, task_id, "user", f"Approval denied: {str(reason).strip()[:500]}")
        return {
            "ok": True, "task_id": task_id, "approval_id": approval_id,
            "decision": "denied", "resumed": False,
            "status": kb.get_task(conn, task_id).status,
        }
    raise ValueError("decision must be approve or deny")