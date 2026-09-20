"""Session-scoped roster and bounded live transcript snapshots for shared clients.

Async projection adapted from JoaoMarcos44's PR #70899; controls reuse the
existing subagent.steer RPC rather than introducing a second steering runtime.
"""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method

_SUBAGENT_SNAPSHOT_FIELDS = (
    "subagent_id", "parent_id", "depth", "goal", "delegation_id", "model",
    "started_at", "status", "tool_count", "last_tool", "accepting_steer",
)
_SUBAGENT_TAIL_BYTES = 16384
_DELEGATION_RECOVERY_FIELDS = (
    "delegation_id", "goal", "task_count", "dispatched_at", "completed_at",
)


def _owned_delegation_recovery_receipts(session_id, owner):
    """Read recent process-loss receipts from THIS session's profile and fail closed on ownership.

    The async-delegation ledger is authoritative. This is a sanitized projection for UI recovery:
    no context, toolsets, model, result body, error text, routing metadata, or callbacks cross the wire.
    """
    from tools.async_delegation import list_durable_recovery_receipts

    try:
        with _session_profile_runtime_scope(owner):
            receipts = list_durable_recovery_receipts()
            owned = []
            for receipt in receipts:
                event = {
                    "type": "async_delegation",
                    "origin_ui_session_id": receipt.get("origin_ui_session_id"),
                    "session_key": receipt.get("origin_session"),
                }
                if not _session_owns_notification_event(session_id, owner, event):
                    continue
                projected = {key: receipt.get(key) for key in _DELEGATION_RECOVERY_FIELDS}
                projected.update(status="interrupted", recovery_reason="process_restart")
                owned.append(projected)
            return owned
    except Exception:
        logger.debug("delegation recovery snapshot failed", exc_info=True)
        return []



def _owned_subagent_records(session_id, transport, owner):
    from tools.delegate_tool_registry import _active_subagents, _active_subagents_lock, _subagent_transport_matches

    with _active_subagents_lock:
        return [dict(r) for r in _active_subagents.values()
                if r.get("owner_session_id") == session_id
                and _subagent_transport_matches(r, transport)
                and r.get("owner_session_record") is owner]


@method("subagent.list")
def _(rid, params):
    session_id = _str_param(params, "session_id")
    transport, owner = _current_session_steer_authority(session_id)
    if transport is None or owner is None:
        return _err(rid, 4001, "session not found or not owned by this transport")
    live = _owned_subagent_records(session_id, transport, owner)
    return _ok(rid, {
        "subagents": [{key: r.get(key) for key in _SUBAGENT_SNAPSHOT_FIELDS} for r in live],
        "delegations": _owned_delegation_recovery_receipts(session_id, owner),
    })


@method("subagent.interrupt")
def _(rid, params):
    from agent.interrupt_compat import request_hard_interrupt

    subagent_id = _str_param(params, "subagent_id")
    if not subagent_id:
        return _err(rid, 4000, "subagent_id required")
    session_id = _str_param(params, "session_id")
    transport, owner = _current_session_steer_authority(session_id)
    if transport is None or owner is None:
        return _err(rid, 4001, "session not found or not owned by this transport")
    record = next((r for r in _owned_subagent_records(session_id, transport, owner)
                   if r.get("subagent_id") == subagent_id), None)
    agent = record.get("agent") if record else None
    # Interrupt the authorized object, never re-resolve a globally recyclable id.
    found = False
    if agent is not None:
        try:
            found = bool(request_hard_interrupt(agent, f"Interrupted via TUI ({subagent_id})"))
        except Exception:
            logger.debug("subagent interrupt failed", exc_info=True)
    return _ok(rid, {"found": found, "subagent_id": subagent_id})


@method("subagent.tail")
def _(rid, params):
    session_id = _str_param(params, "session_id")
    subagent_id = _str_param(params, "subagent_id")
    if not subagent_id:
        return _err(rid, 4000, "subagent_id required")
    transport, owner = _current_session_steer_authority(session_id)
    if transport is None or owner is None:
        return _err(rid, 4001, "session not found or not owned by this transport")
    result = {"subagent_id": subagent_id, "available": False, "text": "", "truncated": False}
    record = next((r for r in _owned_subagent_records(session_id, transport, owner)
                   if r.get("subagent_id") == subagent_id), None)
    path = getattr(record.get("agent"), "_live_transcript_path", None) if record else None
    if not path:
        return _ok(rid, result)
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - _SUBAGENT_TAIL_BYTES))
            text = stream.read(_SUBAGENT_TAIL_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        # Creation/cleanup races are normal while a child starts or ends.
        return _ok(rid, result)
    return _ok(rid, {**result, "available": True, "text": text, "truncated": size > _SUBAGENT_TAIL_BYTES})


def register(server):
    bind_module(globals(), server)
