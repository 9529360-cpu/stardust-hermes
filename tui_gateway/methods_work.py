"""Profile work ledger; live children retain exact session/transport authority."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _work_owned_children(params):
    from .methods_subagents import _owned_subagent_records
    session_id = params.get("session_id", "")
    if not session_id:
        return []
    transport, owner = _current_session_steer_authority(session_id)
    if transport is None or owner is None:
        return None
    return _owned_subagent_records(session_id, transport, owner)


@method("work.list")
def _work_list(rid, params):
    from tools.work_ledger import list_work, subagent_work
    children = _work_owned_children(params)
    if children is None:
        return _err(rid, 4001, "session not found or not owned by this transport")
    return _ok(rid, {"work": list_work(include_subagents=False) + [subagent_work(r) for r in children]})


@method("work.cancel")
def _work_cancel(rid, params):
    from tools.work_ledger import cancel_work
    id = params["id"]
    if id.startswith("subagent:"):
        children = _work_owned_children(params)
        if children is None or not params.get("session_id"):
            return _err(rid, 4001, "session not found or not owned by this transport")
        from agent.interrupt_compat import request_hard_interrupt
        record = next((r for r in children if r.get("subagent_id") == id.split(":", 1)[1]), None)
        accepted = bool(record and record.get("agent") and request_hard_interrupt(record["agent"], "work.cancel"))
        return _ok(rid, {"id": id, "status": "interrupt_requested" if accepted else "not_found",
                         "message": "Interruption requested." if accepted else "Unknown work ID."})
    return _ok(rid, cancel_work(id, include_subagents=False))


def register(server):
    bind_module(globals(), server)
