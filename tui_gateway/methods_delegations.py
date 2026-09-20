"""Read-only recovery receipts for process-local background delegations.

Live subagent control stays in methods_subagents. This surface only projects
durable owner-exit receipts so clients can explain work that disappeared across
a backend restart without turning the receipt into another task authority.
"""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


@method("delegation.recovery.list")
def _(rid, params):
    session_id = _str_param(params, "session_id")
    transport, owner = _current_session_steer_authority(session_id)
    if transport is None or owner is None:
        return _err(rid, 4001, "session not found or not owned by this transport")

    from tools.async_delegation import list_durable_recovery_receipts

    try:
        with _session_profile_runtime_scope(owner):
            receipts = list_durable_recovery_receipts()
    except Exception as exc:
        logger.debug("delegation recovery receipt read failed", exc_info=True)
        return _err(rid, 5027, "delegation recovery unavailable")

    visible = []
    for receipt in receipts:
        ownership_event = {
            "type": "async_delegation",
            "origin_ui_session_id": receipt.get("origin_ui_session_id") or "",
            "session_key": receipt.get("origin_session") or "",
        }
        if not _session_owns_notification_event(session_id, owner, ownership_event):
            continue
        visible.append({
            "delegation_id": receipt["delegation_id"],
            "goal": receipt["goal"],
            "task_count": receipt["task_count"],
            "dispatched_at": receipt["dispatched_at"],
            "completed_at": receipt["completed_at"],
            "reason": "owner_exited",
        })

    return _ok(rid, {"receipts": visible})


def register(server):
    bind_module(globals(), server)
