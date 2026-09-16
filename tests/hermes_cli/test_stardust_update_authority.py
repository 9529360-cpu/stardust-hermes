from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_cli.stardust_update as stardust_update
from hermes_cli.subcommands.update import _product_update_handler


def test_product_remote_normalizes_https_and_ssh_forms():
    accepted = (
        "https://github.com/9529360-cpu/stardust-hermes.git",
        "https://github.com/9529360-cpu/stardust-hermes/",
        "git@github.com:9529360-cpu/stardust-hermes.git",
        "ssh://git@github.com/9529360-cpu/stardust-hermes.git",
    )
    for remote in accepted:
        assert stardust_update.is_product_remote(remote) is True

    assert stardust_update.is_product_remote(
        "https://github.com/NousResearch/hermes-agent.git"
    ) is False
    assert stardust_update.is_legacy_upstream_remote(
        "https://github.com/NousResearch/hermes-agent.git"
    ) is True
    assert stardust_update.is_product_remote(
        "https://github.com/someone/stardust-hermes.git"
    ) is False


def test_custom_origin_fails_closed_without_rewriting(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        stardust_update,
        "_git_origin",
        lambda _root: "https://github.com/someone/stardust-hermes.git",
    )

    with pytest.raises(SystemExit) as exc_info:
        stardust_update._require_product_or_legacy_origin(tmp_path, migrate_legacy=True)

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    assert "Stardust update refused" in output
    assert stardust_update.PRODUCT_GIT_URL in output
    assert "someone/stardust-hermes" in output


def test_legacy_origin_is_read_only_for_check(monkeypatch, tmp_path):
    legacy = "https://github.com/NousResearch/hermes-agent.git"
    calls = []
    monkeypatch.setattr(stardust_update, "_git_origin", lambda _root: legacy)
    monkeypatch.setattr(
        stardust_update,
        "_git_run",
        lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert stardust_update._require_product_or_legacy_origin(
        tmp_path, migrate_legacy=False
    ) == legacy
    assert calls == []


def test_legacy_origin_migrates_only_to_product(monkeypatch, tmp_path, capsys):
    legacy = "git@github.com:NousResearch/hermes-agent.git"
    origins = iter((legacy, stardust_update.PRODUCT_GIT_URL))
    calls = []

    monkeypatch.setattr(stardust_update, "_git_origin", lambda _root: next(origins))

    def fake_git_run(root, args, **_kwargs):
        calls.append((root, args))
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(stardust_update, "_git_run", fake_git_run)

    migrated = stardust_update._require_product_or_legacy_origin(
        tmp_path, migrate_legacy=True
    )

    assert migrated == stardust_update.PRODUCT_GIT_URL
    assert calls == [
        (tmp_path, ["remote", "set-url", "origin", stardust_update.PRODUCT_GIT_URL])
    ]
    assert "Migrated legacy product origin" in capsys.readouterr().out


def test_scope_disables_legacy_sources_and_restores_them(monkeypatch):
    import hermes_cli.banner as banner
    import hermes_cli.update_cmd as update_cmd

    def sentinel_is_fork(_url):
        return True

    def sentinel_compare(_current, _target):
        return 99

    monkeypatch.setattr(update_cmd, "_is_fork", sentinel_is_fork)
    monkeypatch.setattr(banner, "_github_compare_behind", sentinel_compare)

    with stardust_update._stardust_updater_scope() as scoped:
        assert scoped is update_cmd
        assert update_cmd._is_fork("https://example.invalid/fork.git") is False
        assert banner._github_compare_behind is stardust_update._product_github_compare_behind

    assert update_cmd._is_fork is sentinel_is_fork
    assert banner._github_compare_behind is sentinel_compare


def test_parser_swaps_only_the_real_main_update_handler():
    def injected(_args):
        return "injected"

    assert _product_update_handler(injected) is injected

    def legacy_main_handler(_args):
        raise AssertionError("the pinned legacy main handler must not run")

    legacy_main_handler.__module__ = "hermes_cli.main"
    legacy_main_handler.__name__ = "cmd_update"

    assert _product_update_handler(legacy_main_handler) is stardust_update.cmd_update


def test_unknown_non_git_install_never_enters_legacy_check(monkeypatch, tmp_path):
    """Unknown non-git installs must not reach the legacy Nous ZIP/API fallback."""
    import hermes_cli.config as config
    import hermes_cli.update_contract as update_contract
    from hermes_cli import main as main_mod

    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(update_contract, "evaluate_update_admission", lambda _root: None)
    monkeypatch.setattr(
        main_mod,
        "_update_preflight_handled",
        lambda _args: pytest.fail("legacy preflight/check must not run for unknown non-git installs"),
    )

    with pytest.raises(SystemExit) as exc_info:
        stardust_update.cmd_update(SimpleNamespace(plan=False, check=True, gateway=False))

    assert exc_info.value.code == 2


def test_product_check_never_enters_legacy_upstream_first_checker(monkeypatch, tmp_path, capsys):
    """A configured `upstream` remote is irrelevant: product check never calls legacy preflight/check."""
    import hermes_cli.config as config
    import hermes_cli.update_contract as update_contract
    from hermes_cli import main as main_mod

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(update_contract, "evaluate_update_admission", lambda _root: None)
    monkeypatch.setattr(
        stardust_update,
        "_git_origin",
        lambda _root: "git@github.com:9529360-cpu/stardust-hermes.git",
    )
    monkeypatch.setattr(
        main_mod,
        "_update_preflight_handled",
        lambda _args: pytest.fail("legacy _cmd_update_check must not run"),
    )
    monkeypatch.setattr(
        stardust_update,
        "stardust_update_status",
        lambda _root, branch="main": {
            "behind": 0,
            "head": "a" * 40,
            "target": "a" * 40,
            "commits": [],
        },
    )

    stardust_update.cmd_update(
        SimpleNamespace(plan=False, check=True, gateway=False, branch=None)
    )

    assert "Already up to date with Stardust" in capsys.readouterr().out


def test_product_status_resolves_stardust_branch_not_git_upstream(monkeypatch, tmp_path):
    head = "1" * 40
    target = "2" * 40
    seen = []

    def fake_git_run(root, args, **_kwargs):
        assert root == tmp_path
        assert args == ["rev-parse", "HEAD"]
        return subprocess.CompletedProcess(["git", *args], 0, stdout=head + "\n", stderr="")

    monkeypatch.setattr(stardust_update, "_git_run", fake_git_run)
    monkeypatch.setattr(
        stardust_update,
        "_product_branch_tip",
        lambda branch="main": seen.append(branch) or target,
    )
    monkeypatch.setattr(
        stardust_update,
        "_product_github_compare_payload",
        lambda current, remote: {
            "ahead_by": 4,
            "commits": [],
            "base": current,
            "head": remote,
        },
    )

    status = stardust_update.stardust_update_status(tmp_path, branch="main")

    assert seen == ["main"]
    assert status["behind"] == 4
    assert status["head"] == head
    assert status["target"] == target


def test_valid_product_checkout_enters_apply_preflight_inside_stardust_scope(monkeypatch, tmp_path):
    import hermes_cli.banner as banner
    import hermes_cli.config as config
    import hermes_cli.update_cmd as update_cmd
    import hermes_cli.update_contract as update_contract
    from hermes_cli import main as main_mod

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(update_contract, "evaluate_update_admission", lambda _root: None)
    monkeypatch.setattr(
        stardust_update,
        "_git_origin",
        lambda _root: "git@github.com:9529360-cpu/stardust-hermes.git",
    )

    original_is_fork = update_cmd._is_fork
    original_compare = banner._github_compare_behind
    seen = []

    def handled(_args):
        seen.append(
            (
                update_cmd._is_fork("https://github.com/NousResearch/hermes-agent.git"),
                banner._github_compare_behind is stardust_update._product_github_compare_behind,
            )
        )
        return True

    monkeypatch.setattr(main_mod, "_update_preflight_handled", handled)

    stardust_update.cmd_update(SimpleNamespace(plan=False, check=False, gateway=False))

    assert seen == [(False, True)]
    assert update_cmd._is_fork is original_is_fork
    assert banner._github_compare_behind is original_compare


def test_release_workflow_is_repo_scoped_and_main_gated():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "stardust-release.yml").read_text(encoding="utf-8")

    assert "github.repository == '9529360-cpu/stardust-hermes'" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "gh release" in workflow
    assert "Stardust $TAG" in workflow
    assert "NousResearch/hermes-agent" not in workflow
