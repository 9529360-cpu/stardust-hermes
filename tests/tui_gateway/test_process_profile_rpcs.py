"""Gateway process RPCs must honor owner homes even when stored session keys collide."""

from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
from tools.process_registry import ProcessRegistry
from tui_gateway import server


def test_process_rpc_profile_scope_and_session_owner(tmp_path, monkeypatch):
    from tools import process_registry as process_module

    root = tmp_path / "home"
    other = root / "profiles" / "other"
    other.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    registry = ProcessRegistry()
    monkeypatch.setattr(process_module, "process_registry", registry)
    sessions = {}
    for home, name in ((root, "launch"), (other, "other")):
        token = set_hermes_home_override(home)
        try:
            proc = registry._new_session(f"private-{name}", "same-task", "same-task", "same-key", None)
            registry._running[proc.id] = proc
            sessions[name] = proc
        finally:
            reset_hermes_home_override(token)
    assert sessions["other"].owner_home == hermes_home_key(other)

    runtime = {"session_key": "same-key", "profile_home": str(other)}
    monkeypatch.setattr(server, "_sess", lambda params, rid: (runtime, None))
    monkeypatch.setattr(server, "_session_processes", lambda session: registry.list_sessions(session_key=session["session_key"]))
    call = lambda method, params: server._methods[method](1, params)

    launch_list = call("agents.list", {})["result"]["processes"]
    other_list = call("agents.list", {"profile": "other"})["result"]["processes"]
    assert [p["session_id"] for p in launch_list] == [sessions["launch"].id]
    assert [p["session_id"] for p in other_list] == [sessions["other"].id]
    assert [p["session_id"] for p in call("process.list", {"session_id": "runtime"})["result"]["processes"]] == [sessions["other"].id]
    assert call("process.kill", {"session_id": "runtime", "process_id": sessions["launch"].id})["error"]["code"] == 4044
    killed = []
    monkeypatch.setattr(registry, "kill_process", lambda sid, **kw: (killed.append(sid) or {"status": "killed"}))
    assert call("process.stop", {"profile": "other"})["result"]["killed"] == 1
    assert killed == [sessions["other"].id]
    killed.clear()
    assert call("process.stop", {"session_id": "runtime"})["result"]["killed"] == 1
    assert killed == [sessions["other"].id]
    killed.clear()
    assert call("process.stop", {})["result"]["killed"] == 1
    assert killed == [sessions["launch"].id]
    killed.clear()
    assert server._mirror_slash_side_effects("runtime", runtime, "/stop") == ""
    assert killed == [sessions["other"].id]
