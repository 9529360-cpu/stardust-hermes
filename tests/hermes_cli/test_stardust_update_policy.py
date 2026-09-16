"""Cross-surface regressions for the pinned Stardust update contract."""

import asyncio


def test_stardust_apply_policy_is_an_explicit_refusal():
    from hermes_cli.update_contract import stardust_apply_refusal

    refusal = stardust_apply_refusal()
    assert refusal is not None
    assert refusal.code == "stardust-pinned"
    assert "pinned" in refusal.message.lower()


def test_dashboard_passive_status_never_calls_update_probe(tmp_path, monkeypatch):
    from hermes_cli import banner
    from hermes_cli.web_routers import actions

    monkeypatch.setattr(actions, "_dashboard_local_update_managed_externally", lambda: False)
    monkeypatch.setattr(actions, "detect_install_method", lambda _root: "git")
    monkeypatch.setattr(actions, "_server_path", lambda _name: tmp_path)

    def unexpected_check(**_kwargs):
        raise AssertionError("passive dashboard status reached upstream update probing")

    monkeypatch.setattr(banner, "check_for_updates", unexpected_check)
    result = asyncio.run(actions.check_hermes_update(force=False))

    assert result["can_apply"] is False
    assert result["update_available"] is False
    assert result["behind"] is None
    assert "pinned" in result["message"].lower()


def test_dashboard_forced_check_is_read_only_but_can_compare(tmp_path, monkeypatch):
    from hermes_cli import banner
    from hermes_cli.web_routers import actions

    monkeypatch.setattr(actions, "_dashboard_local_update_managed_externally", lambda: False)
    monkeypatch.setattr(actions, "detect_install_method", lambda _root: "git")
    monkeypatch.setattr(actions, "_server_path", lambda _name: tmp_path)
    monkeypatch.setattr(actions, "get_hermes_home", lambda: tmp_path)

    calls = []

    def explicit_check(**kwargs):
        calls.append(kwargs)
        return 2

    monkeypatch.setattr(banner, "check_for_updates", explicit_check)
    monkeypatch.setattr(banner, "upstream_commits_behind", lambda: [])

    result = asyncio.run(actions.check_hermes_update(force=True))

    assert calls == [{"passive": False}]
    assert result["behind"] == 2
    assert result["update_available"] is True
    assert result["can_apply"] is False


def test_dashboard_apply_refuses_before_spawning_noop_child(monkeypatch):
    from hermes_cli import update_contract
    from hermes_cli.web_routers import actions

    monkeypatch.setattr(actions, "_dashboard_local_update_managed_externally", lambda: False)

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError("Stardust dashboard attempted to spawn hermes update")

    monkeypatch.setattr(actions, "_spawn_hermes_action", unexpected_spawn)
    monkeypatch.setattr(
        actions,
        "_update_refused",
        lambda error, message, command: {
            "ok": False,
            "error": error,
            "message": message,
            "update_command": command,
        },
    )
    receipts = []
    monkeypatch.setattr(update_contract, "record_refusal_receipt", lambda refusal: receipts.append(refusal.code))

    result = asyncio.run(actions.update_hermes())

    assert result["ok"] is False
    assert result["error"] == "stardust_update_disabled"
    assert receipts == ["stardust-pinned"]
