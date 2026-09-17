import json
from types import SimpleNamespace


def test_status_cron_section_surfaces_stalled_builtin_ticker(tmp_path, monkeypatch, capsys):
    """Active jobs plus a live gateway must not hide a scheduler whose heartbeat is stale."""
    import hermes_cli.cron as cron_cli
    import hermes_cli.status as status

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "ops-job", "enabled": True}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(status, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(cron_cli, "_active_cron_provider_name", lambda: "builtin")
    monkeypatch.setattr(cron_cli, "_builtin_gateway_liveness", lambda: True)
    monkeypatch.setattr("cron.jobs.get_ticker_heartbeat_age", lambda: 999.0)
    monkeypatch.setattr("cron.jobs.get_ticker_success_age", lambda: 999.0)

    status._render_cron(SimpleNamespace(config={}))

    output = capsys.readouterr().out.lower()
    assert "1 active" in output
    assert "ticker" in output and "stalled" in output
