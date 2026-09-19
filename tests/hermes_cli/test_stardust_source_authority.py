from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STARDUST_INSTALL_BASE = "raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust"
UPSTREAM_INSTALL_HOST = "hermes-agent.nousresearch.com/install."


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
    assert 'STARDUST_REF="${STARDUST_INSTALL_REF:-${ARG_REF:-main}}"' in source


def test_shell_bootstrap_presents_stardust_identity() -> None:
    source = _read("scripts/install-stardust.sh")

    assert "s#Hermes Agent Installer#Stardust Personal Assistant Installer#g" in source
    assert "Personal assistant built on the Hermes Agent foundation." in source
    assert "s#Download Hermes Agent#Download Stardust#g" in source


def test_shell_bootstrap_normalizes_tauri_stage_protocol() -> None:
    source = _read("scripts/install-stardust.sh")

    expected_mappings = {
        "-Manifest": "--manifest",
        "-Stage": "--stage",
        "-NonInteractive": "--non-interactive",
        "-Json": "--json",
        "-IncludeDesktop": "--include-desktop",
        "-Commit": "--commit",
        "-Tag": "--tag",
        "-Branch": "--branch",
    }
    for powershell_flag, posix_flag in expected_mappings.items():
        assert f'{powershell_flag}) NORMALIZED_ARGS+=("{posix_flag}")' in source

    assert 'exec /bin/bash "$TMP_INSTALLER" "${NORMALIZED_ARGS[@]}"' in source


def test_powershell_bootstrap_rewrites_repo_prefix_and_fails_closed() -> None:
    source = _read("scripts/install-stardust.ps1")

    # Prefix replacement closes the archive/refs/heads/<branch>.zip fallback too.
    assert "$Source.Replace('https://github.com/NousResearch/hermes-agent', 'https://github.com/9529360-cpu/stardust-hermes')" in source
    assert "$ForbiddenSourceUrls" in source
    assert "upstream product-source URL survived rewriting" in source
    assert "$ArgRef = Get-StardustInstallRef $args" in source


def test_powershell_bootstrap_presents_stardust_identity() -> None:
    source = _read("scripts/install-stardust.ps1")

    assert "$Source.Replace('Hermes Agent Installer', 'Stardust Personal Assistant Installer')" in source
    assert "Personal assistant built on the Hermes Agent foundation." in source
    assert "$Source.Replace('Download Hermes Agent', 'Download Stardust')" in source


def test_desktop_update_authority_is_stardust() -> None:
    source = _read("apps/desktop/electron/update-remote.test.ts")

    assert "https://github.com/9529360-cpu/stardust-hermes.git" in source
    assert "git@github.com:9529360-cpu/stardust-hermes.git" in source


def test_desktop_bootstrap_downloads_stardust_wrapper() -> None:
    source = _read("apps/desktop/electron/bootstrap-runner.ts")

    assert "const STARDUST_SOURCE_REPO = '9529360-cpu/stardust-hermes'" in source
    assert "install-stardust.ps1" in source
    assert "install-stardust.sh" in source
    assert "raw.githubusercontent.com/${STARDUST_SOURCE_REPO}/${ref}/scripts/${scriptName}" in source
    assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in source


def test_tauri_bootstrap_downloads_stardust_wrapper() -> None:
    source = _read("apps/bootstrap-installer/src-tauri/src/install_script.rs")

    assert 'Self::Ps1 => "install-stardust.ps1"' in source
    assert 'Self::Sh => "install-stardust.sh"' in source
    assert "raw.githubusercontent.com/9529360-cpu/stardust-hermes" in source
    assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in source


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


def test_public_readmes_install_stardust_not_upstream() -> None:
    readmes = ("README.md", "README.zh-CN.md", "README.es.md", "README.ur-pk.md")
    for path in readmes:
        source = _read(path)
        assert STARDUST_INSTALL_BASE in source
        assert "hermes-agent.nousresearch.com/install.sh" not in source
        assert "hermes-agent.nousresearch.com/install.ps1" not in source
        assert "raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install" not in source


def test_public_install_docs_only_advertise_stardust_bootstrap() -> None:
    install_docs = (
        "website/docs/index.mdx",
        "website/docs/getting-started/installation.md",
        "website/docs/getting-started/quickstart.md",
        "website/docs/getting-started/termux.md",
        "website/docs/developer-guide/contributing.md",
    )
    for path in install_docs:
        source = _read(path)
        assert STARDUST_INSTALL_BASE in source
        assert UPSTREAM_INSTALL_HOST not in source
        assert "raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install" not in source


def test_llms_index_is_canonical_to_stardust() -> None:
    source = _read("website/scripts/generate-llms-txt.py")

    assert 'REPO_URL = "https://github.com/9529360-cpu/stardust-hermes"' in source
    assert "install-stardust.sh" in source
    assert "hermes-agent.nousresearch.com/docs" not in source
    assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in source


def test_uninstall_reinstall_guidance_stays_on_stardust() -> None:
    source = _read("hermes_cli/uninstall.py")

    assert STARDUST_INSTALL_BASE in source
    assert UPSTREAM_INSTALL_HOST not in source
    assert "Thank you for using Stardust!" in source

def test_runtime_catalog_sources_belong_to_stardust() -> None:
    model_catalog = _read("hermes_cli/model_catalog.py")
    config_defaults = _read("hermes_cli/config_defaults.py")
    local_catalog = _read("hermes_cli/local_runtime/catalog.py")
    expected = "raw.githubusercontent.com/9529360-cpu/stardust-hermes/main"

    for source in (model_catalog, config_defaults, local_catalog):
        assert expected in source
        assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in source
        assert "hermes-agent.nousresearch.com/docs/api/" not in source


def test_desktop_unsupported_update_routes_to_stardust_source() -> None:
    source = _read("apps/desktop/src/app/updates-overlay.tsx")

    assert "PRODUCT_REPOSITORY_URL" in source
    assert "https://hermes-agent.nousresearch.com/" not in source

def test_desktop_does_not_surface_nous_diagnostics_upload() -> None:
    sources = (
        _read("apps/desktop/src/components/error-boundary.tsx"),
        _read("apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx"),
        _read("apps/desktop/src/app/contrib/wiring.tsx"),
    )

    assert all("requestSendDiagnostics" not in source for source in sources)
    assert all("SendDiagnosticsHost" not in source for source in sources)

def test_desktop_first_run_identity_is_stardust_owned() -> None:
    source = _read("apps/desktop/src/store/onboarding-script.ts")
    kickoff = _read("apps/desktop/src/app/contrib/onboarding-kickoff.ts")

    assert "You are Stardust" in source
    assert "you are Stardust" in source
    assert "free Nous account" not in source
    assert "You are Hermes" not in source
    assert "record.free_tier !== true" not in kickoff


def test_desktop_ssh_recovery_installs_stardust_source() -> None:
    source = _read("apps/desktop/electron/remote-lifecycle.ts")

    assert STARDUST_INSTALL_BASE in source
    assert UPSTREAM_INSTALL_HOST not in source




def test_normal_stardust_setup_never_offers_nous_telemetry() -> None:
    setup = _read("hermes_cli/setup.py")
    tools = _read("hermes_cli/tools_config.py")
    defaults = _read("hermes_cli/config_defaults.py")
    example = _read("cli-config.yaml.example")

    assert "Send shared metrics to Nous?" not in setup
    assert "sending to Nous" not in tools
    assert "telemetry.nousresearch.com" not in defaults
    assert "telemetry.nousresearch.com" not in example



def test_runtime_recovery_guidance_stays_on_stardust_source() -> None:
    constants = _read("hermes_constants.py")
    assert STARDUST_INSTALL_BASE in constants
    assert "STARDUST_REPOSITORY_URL" in constants
    assert "If that also fails, reinstall: https://hermes-agent.nousresearch.com" not in constants

    update = _read("hermes_cli/update_cmd.py")
    maintenance = _read("hermes_cli/update_cmd_maint.py")
    zip_update = _read("hermes_cli/update_cmd_zip.py")

    assert "STARDUST_INSTALL_SH_URL" in update
    assert "hermes-agent.nousresearch.com/install.sh" not in update

    for source in (maintenance, zip_update):
        assert "STARDUST_INSTALL_" in source
        assert "hermes-agent.nousresearch.com/install.sh" not in source
        assert "hermes-agent.nousresearch.com/install.ps1" not in source
    assert "reinstall from https://hermes-agent.nousresearch.com" not in zip_update
