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


def test_cli_update_entrypoint_cannot_reactivate_upstream_sync() -> None:
    source = _read("hermes_cli/main.py")
    marker = "def cmd_update(args):"
    assert marker in source

    block = source.split(marker, 1)[1].split("\ndef ", 1)[0]
    assert "disabled for the pinned Stardust local edition" in block
    assert "_cmd_update_impl" not in block
    assert "_sync_with_upstream_if_needed" not in block


def test_security_reports_belong_to_stardust() -> None:
    for path in ("SECURITY.md", "SECURITY.es.md"):
        source = _read(path)
        assert "9529360-cpu/stardust-hermes/security/advisories/new" in source
        assert "NousResearch/hermes-agent/security/advisories/new" not in source
        assert "security@nousresearch.com" not in source
