from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("prune_builtins", "scope"),
    [
        (True, "agent-created skills plus unused bundled built-ins"),
        (False, "agent-created skills"),
    ],
)
def test_first_run_notice_reports_effective_curator_scope(
    monkeypatch, capsys, prune_builtins, scope
):
    from agent import curator
    from hermes_cli.update_cmd_maint import _print_curator_first_run_notice

    monkeypatch.setattr(curator, "is_enabled", lambda: True)
    monkeypatch.setattr(curator, "load_state", lambda: {})
    monkeypatch.setattr(curator, "get_interval_hours", lambda: 24 * 7)
    monkeypatch.setattr(curator, "get_prune_builtins", lambda: prune_builtins)

    _print_curator_first_run_notice()

    output = capsys.readouterr().out
    assert "First pass is deferred ~7d" in output
    assert f"scope: {scope}" in output
    assert "auto-deleted" in output
