"""Profile-scoped, read-only Task Center RPC."""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _tasks_list(rid, params):
    from hermes_constants import (
        named_profile_is_deleted,
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from tools.task_projection import list_task_rows

    profile = str(params.get("profile") or "").strip()
    raw_limit = params.get("limit", 20)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        return _err(rid, 4000, "invalid params for tasks.list: limit must be an integer")
    try:
        home = _profile_home(profile)
    except ProfileUnavailableError:
        return _err(rid, 4064, f"profile '{profile}' not found")
    token = set_hermes_home_override(str(home)) if home is not None else None
    try:
        if home is not None and named_profile_is_deleted(home):
            return _err(rid, 4064, f"profile '{profile}' not found")
        result = list_task_rows(limit=raw_limit)
        result["scoped"] = _ran_under_profile_name()
        return _ok(rid, result)
    except Exception:
        return _err(rid, 5025, "tasks.list failed")
    finally:
        if token is not None:
            reset_hermes_home_override(token)


method("tasks.list")(_tasks_list)


def register(server):
    bind_module(globals(), server)
    server._LONG_HANDLERS = server._LONG_HANDLERS | {"tasks.list"}
