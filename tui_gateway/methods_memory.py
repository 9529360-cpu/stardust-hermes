"""User-managed memory RPCs. Fresh stores avoid changing live prompt snapshots.

These are explicit user operations, not agent proposals: no agent write-approval
staging is needed, but privacy flags, scanning, budgets and file guards still apply.
"""

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()

def method(name):
    def decorate(fn):
        def validated(rid, params):
            from pydantic import ValidationError
            from tui_gateway.contracts.registry import METHODS

            try:
                METHODS[name].params.model_validate(params)
            except ValidationError as exc:
                return _err(rid, 4000, str(exc))
            return fn(rid, params)
        return _registry.method(name)(validated)
    return decorate


def _memory_rpc_store():
    from tools.memory_tool import load_on_disk_store

    return load_on_disk_store()


def _memory_rpc_result(rid, result):
    if not result.get("success"):
        return _err(rid, 4000, result.get("error", "Memory operation failed."))
    return _ok(rid, {"success": True})


@method("memory.list")
def _(rid, params):
    store = _memory_rpc_store()
    target = params.get("target", "both")
    targets = ("memory", "user") if target == "both" else (target,)
    return _ok(rid, {"entries": [
        {"target": name, "index": i, "text": text}
        for name in targets
        for i, text in enumerate(store.user_entries if name == "user" else store.memory_entries)
    ]})


@method("memory.remember")
def _(rid, params):
    from tools.memory_tool import _memory_target_error

    store = _memory_rpc_store()
    target = params["target"]
    error = _memory_target_error(store, target)
    return _memory_rpc_result(rid, error or store.add(target, params["content"]))


@method("memory.forget")
def _(rid, params):
    from tools.memory_tool import _memory_target_error

    index, text = params.get("index"), params.get("text")
    if (index is None) == (text is None):
        return _err(rid, 4000, "Provide exactly one of index or text.")
    expected_text = params.get("expected_text")
    if index is not None and (not isinstance(expected_text, str) or not expected_text):
        return _err(rid, 4000, "Index requires expected_text from memory.list.")
    if text is not None and expected_text is not None:
        return _err(rid, 4000, "expected_text is only valid with index.")
    store = _memory_rpc_store()
    target = params["target"]
    error = _memory_target_error(store, target)
    if error:
        return _memory_rpc_result(rid, error)
    result = (store.remove_index(target, index, expected_text) if index is not None
              else store.remove_exact(target, text))
    return _memory_rpc_result(rid, result)


def register(server):
    bind_module(globals(), server, skip=("_",))
