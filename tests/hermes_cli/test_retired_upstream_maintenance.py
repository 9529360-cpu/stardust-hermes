"""Retired upstream services must not run during local skill maintenance."""
import sys
import types


def test_serve_maintenance_does_not_pull_upstream_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    import agent.curator as curator
    import hermes_cli.profiles as profiles
    import hermes_cli.web_server_sessions as sessions

    calls = []
    monkeypatch.setattr(profiles, "_check_gateway_running", lambda *_: False)
    monkeypatch.setattr(sessions, "_skill_maintenance_idle_for", lambda *_: 60)
    monkeypatch.setattr(curator, "maybe_run_curator", lambda **_: calls.append("curator"))
    personal = types.ModuleType("tools.skills_sync_client")
    personal.maybe_pull_skills = lambda: calls.append("upstream-personal")
    org = types.ModuleType("tools.skills_sync_client_org")
    org.maybe_pull_org_skills = lambda: calls.append("upstream-org")
    monkeypatch.setitem(sys.modules, personal.__name__, personal)
    monkeypatch.setitem(sys.modules, org.__name__, org)

    sessions._maybe_run_skill_maintenance(0)

    assert calls == ["curator"]


def test_cli_startup_keeps_curator_without_upstream_sync(monkeypatch):
    import agent.curator as curator
    from cli import HermesCLI

    calls = []
    monkeypatch.setattr(curator, "maybe_run_curator", lambda **_: calls.append("curator"))
    for module_name, function in (
        ("tools.skills_sync_client", "maybe_pull_skills"),
        ("tools.skills_sync_client_org", "maybe_pull_org_skills"),
    ):
        module = types.ModuleType(module_name)
        setattr(module, function, lambda: calls.append("upstream"))
        monkeypatch.setitem(sys.modules, module_name, module)
    instance = types.SimpleNamespace(_console_print=lambda *_: None)
    HermesCLI._tui_startup_background_maintenance(instance)
    assert calls == ["curator"]
