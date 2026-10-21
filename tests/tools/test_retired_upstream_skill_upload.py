"""Local skill writes do not schedule retired upstream replication."""


def test_skill_write_does_not_schedule_upstream_upload(monkeypatch):
    import tools.skill_manager_tool as manager
    import tools.skill_usage as usage

    timers = []
    usage.save_usage({"missing-test-skill": {"created_by": "agent", "sync": True}})  # historical opt-in
    import threading
    monkeypatch.setattr(threading, "Timer", lambda *a, **kw: timers.append((a, kw)))
    manager._record_success(
        "patch", "missing-test-skill", {}, file_path=None, absorbed_into=None,
        curator_managed=False, task_id=None, session_id=None, ledger_before=[])
    assert timers == []
