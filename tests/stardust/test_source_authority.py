from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_cmd_installer_never_dispatches_to_upstream() -> None:
    source = _read("scripts/install.cmd")

    assert "install-stardust.ps1" in source
    assert "hermes-agent.nousresearch.com/install.ps1" not in source
    assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in source


def test_shell_bootstrap_rewrites_repo_prefix_and_fails_closed() -> None:
    source = _read("scripts/install-stardust.sh")

    # Prefix replacement covers .git, archive/zip, and future repository paths.
    assert "s#https://github.com/NousResearch/hermes-agent#https://github.com/9529360-cpu/stardust-hermes#g" in source
    assert "raw.githubusercontent.com/NousResearch/hermes-agent" in source
    assert "upstream product-source URL survived rewriting" in source
    assert "grep -Eq" in source


def test_powershell_bootstrap_rewrites_repo_prefix_and_fails_closed() -> None:
    source = _read("scripts/install-stardust.ps1")

    # Prefix replacement closes the archive/refs/heads/<branch>.zip fallback too.
    assert "$Source.Replace('https://github.com/NousResearch/hermes-agent', 'https://github.com/9529360-cpu/stardust-hermes')" in source
    assert "$ForbiddenSourceUrls" in source
    assert "upstream product-source URL survived rewriting" in source


def test_desktop_update_authority_is_stardust() -> None:
    source = _read("apps/desktop/electron/update-remote.test.ts")

    assert "https://github.com/9529360-cpu/stardust-hermes.git" in source
    assert "git@github.com:9529360-cpu/stardust-hermes.git" in source
