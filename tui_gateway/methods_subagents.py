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
        "delegations": [],
    })


@method("delegation.recovery.list")
def _(rid, params):
    """Display-only receipts for this session's background work whose owner process exited.

    Identity comes from the authorized live record (durable ``session_key`` + ``profile_home``), never
    from params; ``subagent.list`` keeps ``delegations: []`` because async units lack generation authority."""
    from tools.async_delegation import list_unknown_delegation_receipts

    transport, owner = _current_session_steer_authority(_str_param(params, "session_id"))
    if transport is None or owner is None:
        return _err(rid, 4001, "session not found or not owned by this transport")
    try:
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        home_token = set_hermes_home_override(owner.get("profile_home")) if owner.get("profile_home") else None
        try:
            compression_lineage = _get_db().get_compression_lineage(str(owner.get("session_key") or ""))
            from tools import async_delegation
            async_delegation.recover_abandoned_delegations()
        finally:
            if home_token is not None:
                reset_hermes_home_override(home_token)
        receipts = list_unknown_delegation_receipts(
            session_key=str(owner.get("session_key") or ""), profile_home=owner.get("profile_home"),
            compression_lineage=compression_lineage)
    except Exception:
        logger.warning("delegation recovery receipts unavailable", exc_info=True)
        return _err(rid, 5000, "recovery receipts unavailable")
    return _ok(rid, {"receipts": receipts})


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
    record = next((r for r in _owned_subagent_records(session_id, transport, owner)
                   if r.get("subagent_id") == subagent_id), None)
    from tools.delegate_tool_observation import read_child_activity
    return _ok(rid, {"subagent_id": subagent_id, **read_child_activity(record)})


def register(server):
    bind_module(globals(), server)
