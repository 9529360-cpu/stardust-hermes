def test_doctor_host_resource_check_reports_critical_pressure(monkeypatch, capsys):
    """Doctor must surface the same critical host pressure used by status/monitoring."""
    import hermes_cli.doctor as doctor
    import hermes_cli.doctor_platform as platform

    check = getattr(platform, "_check_host_resources", None)
    assert callable(check), "doctor is missing a host-resource operational check"
    assert any(title == "Host Resources" and fn is check for title, fn in doctor.DOCTOR_CHECKS)

    monkeypatch.setattr(
        "gateway.disk_status.collect_disk_status",
        lambda: {"pressure": "critical", "free_mb": 96, "used_percent": 99.1},
    )
    monkeypatch.setattr(
        "gateway.memory_status.collect_memory_status",
        lambda: {"pressure": "critical", "system_available_mb": 128, "gateway_rss_mb": 900, "swap_used_mb": 256},
    )

    finding = check(False)
    output = capsys.readouterr().out.lower()

    assert "disk pressure critical" in output
    assert "memory pressure critical" in output
    assert len(finding.manual_issues) == 2
