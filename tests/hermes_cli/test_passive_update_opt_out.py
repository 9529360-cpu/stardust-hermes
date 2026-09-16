"""Pinned Stardust update policy: automatic checks off, explicit comparison still available."""
import subprocess

from hermes_constants import get_hermes_home


def test_stardust_passive_check_returns_before_repo_or_network_probe(monkeypatch):
    from hermes_cli import banner

    # Legacy/upstream config can still contain an explicit opt-in. Stardust product policy wins:
    # normal startup/banner/version paths must not contact any repository or upstream service.
    config = get_hermes_home() / "config.yaml"
    config.write_text("updates:\n  check: true\n", encoding="utf-8")

    def unexpected_repo_probe():
        raise AssertionError("passive update check reached repository/network probing")

    monkeypatch.setattr(banner, "_resolve_repo_dir", unexpected_repo_probe)
    assert banner.check_for_updates(passive=True) is None


def test_explicit_check_fetches_local_origin_despite_passive_policy(tmp_path, monkeypatch, capsys):
    from hermes_cli import main
    from hermes_cli.update_cmd import _cmd_update_check

    remote = tmp_path / "remote"
    local = tmp_path / "checkout"

    def git(*args):
        return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True)

    git("init", "-b", "main", remote)
    git("-C", remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "initial")
    git("clone", remote, local)
    git("-C", remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "next")
    monkeypatch.setattr(main, "PROJECT_ROOT", local)

    # Explicit comparison is a maintainer action and intentionally bypasses the passive policy.
    _cmd_update_check()
    output = capsys.readouterr().out
    assert "Fetching from origin" in output
    assert "1 commit" in output
