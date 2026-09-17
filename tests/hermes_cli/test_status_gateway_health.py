from types import SimpleNamespace


def test_status_gateway_section_surfaces_runtime_health(monkeypatch, capsys):
    """The top-level operator overview must not hide a wedged gateway behind a live PID."""
    import hermes_cli.gateway as gateway
    import hermes_cli.gateway_multiplex_served as multiplex
    import hermes_cli.status as status

    monkeypatch.setattr(
        gateway,
        "get_gateway_runtime_snapshot",
        lambda: gateway.GatewayRuntimeSnapshot(manager="manual", gateway_pids=(4242,)),
    )
    monkeypatch.setattr(gateway, "named_profile_served_by_running_multiplexer", lambda: False)
    monkeypatch.setattr(gateway, "_runtime_health_lines", lambda: [
        "Gateway PID 4242 is alive but its event loop is unresponsive (wedged)"
    ])
    monkeypatch.setattr(multiplex, "multiplexer_served_secondaries", lambda: [])
    monkeypatch.setattr(status, "_is_termux", lambda: False)

    status._render_gateway(SimpleNamespace(config={}))

    output = capsys.readouterr().out
    assert "Gateway Service" in output
    assert "running" in output.lower()
    assert "event loop is unresponsive" in output
