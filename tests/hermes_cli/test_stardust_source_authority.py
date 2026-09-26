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


def test_repository_and_docker_default_soul_match_runtime_seed() -> None:
    from hermes_cli.default_soul import DEFAULT_SOUL_MD

    for path in ("SOUL.md", "docker/SOUL.md"):
        source = _read(path).strip()
        assert source == DEFAULT_SOUL_MD
        assert "Hermes Agent, built by Nous Research" not in source


def test_installers_seed_soul_from_canonical_repository_source() -> None:
    shell = _read("scripts/install.sh")
    powershell = _read("scripts/install.ps1")

    assert 'cp "$INSTALL_DIR/SOUL.md" "$HERMES_HOME/SOUL.md"' in shell
    assert '$canonicalSoulPath = "$InstallDir\\SOUL.md"' in powershell
    assert "Copy-Item -LiteralPath $canonicalSoulPath -Destination $soulPath" in powershell
    assert "You are Hermes Agent, built by Nous Research" not in shell
    assert "You are Hermes Agent, built by Nous Research" not in powershell


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


def test_cli_update_entrypoint_uses_stardust_transaction() -> None:
    source = _read("hermes_cli/main.py")
    marker = "def cmd_update(args):"
    assert marker in source

    block = source.split(marker, 1)[1].split("\ndef ", 1)[0]
    assert "return _run_update_transaction(args)" in block
    assert "pinned Stardust local edition" not in block


def test_update_transaction_authority_is_stardust() -> None:
    source = _read("hermes_cli/update_cmd_git.py")
    assert 'OFFICIAL_REPO_URL = "https://github.com/9529360-cpu/stardust-hermes.git"' in source
    assert '"git@github.com:9529360-cpu/stardust-hermes.git"' in source
    block = source.split("OFFICIAL_REPO_URLS =", 1)[1].split("SKIP_UPSTREAM_PROMPT_FILE", 1)[0]
    assert "NousResearch/hermes-agent" not in block


def test_desktop_update_checks_are_not_pinned_off() -> None:
    source = _read("apps/desktop/electron/main.ts")
    assert "STARDUST_LOCAL_EDITION" not in source
    assert "reason: 'local-edition'" not in source
    assert "async function checkUpdates" in source
    assert "async function applyUpdates" in source


def test_legacy_nous_upstream_is_not_accepted_as_authority(monkeypatch) -> None:
    from hermes_cli import update_cmd_git

    monkeypatch.setattr(
        update_cmd_git,
        "_git_stdout",
        lambda *_args, **_kwargs: "https://github.com/NousResearch/hermes-agent.git",
    )
    assert update_cmd_git._has_upstream_remote(["git"], ROOT) is False


def test_sqlite_remediation_reinstall_hint_targets_stardust() -> None:
    """The post-update SQLite-remediation reinstall command, specifically — NOT every
    string in this file: a separate, still-unresolved docs-link migration (issue tracked
    outside this test) also touches update_cmd_maint.py and is out of scope here."""
    from hermes_cli import update_cmd_maint

    assert update_cmd_maint._REINSTALL_ONE_LINER == {
        True: (
            "iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes"
            "/main/scripts/install-stardust.ps1)"
        ),
        False: (
            "curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes"
            "/main/scripts/install-stardust.sh | bash"
        ),
    }


def test_legacy_upstream_is_repaired_to_stardust(monkeypatch) -> None:
    from hermes_cli import update_cmd_git

    calls = []
    monkeypatch.setattr(update_cmd_git, "_git_stdout", lambda *_args, **_kwargs: "https://github.com/NousResearch/hermes-agent.git")
    monkeypatch.setattr(update_cmd_git, "_git_ok", lambda _git, args, _cwd, **_kw: calls.append(args) or True)

    assert update_cmd_git._add_upstream_remote(["git"], ROOT) is True
    assert calls == [[
        "remote",
        "set-url",
        "upstream",
        "https://github.com/9529360-cpu/stardust-hermes.git",
    ]]


def test_banner_update_check_authority_is_stardust() -> None:
    from hermes_cli import banner

    assert banner._OFFICIAL_REPO_URL == "https://github.com/9529360-cpu/stardust-hermes.git"
    assert banner._OFFICIAL_REPO_CANONICAL == "github.com/9529360-cpu/stardust-hermes"
    assert banner._RELEASE_URL_BASE == "https://github.com/9529360-cpu/stardust-hermes/releases/tag"

    source = _read("hermes_cli/banner.py")
    assert "NousResearch/hermes-agent" not in source
    assert "nousresearch/hermes-agent" not in source


def test_banner_compare_api_is_scoped_to_the_checkouts_own_repo(monkeypatch) -> None:
    """The exact-behind-count compare call must target whichever repo the two tips came
    from, never a hardcoded repo — a stale hardcode 404s on any post-fork Stardust SHA
    (or worse, silently compares against an unrelated repo's history)."""
    import urllib.request

    from hermes_cli import banner

    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"ahead_by": 3}'

        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    banner._compare_payload_cache.clear()

    sha_a, sha_b = "a" * 40, "b" * 40
    behind = banner._tips_behind(sha_a, sha_b, repo_slug="9529360-cpu/stardust-hermes")

    assert behind == 3
    assert captured["url"] == (
        f"https://api.github.com/repos/9529360-cpu/stardust-hermes/compare/{sha_a}...{sha_b}"
    )


def test_banner_compare_skips_api_call_without_a_repo_slug(monkeypatch) -> None:
    """A non-GitHub origin has no repo slug to compare against; this must degrade to the
    honest no-count sentinel instead of querying some other (wrong) repo by default."""
    import urllib.request

    from hermes_cli import banner

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("must not call the GitHub compare API without a repo_slug")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    behind = banner._tips_behind("a" * 40, "b" * 40, repo_slug=None)

    assert behind == banner.UPDATE_AVAILABLE_NO_COUNT


def test_zip_fallback_archive_url_targets_stardust_repo() -> None:
    from hermes_cli import update_cmd_git, update_cmd_zip

    assert update_cmd_zip._zip_archive_url("main") == (
        "https://github.com/9529360-cpu/stardust-hermes/archive/refs/heads/main.zip"
    )
    # Same authority as the primary git update path — not a second, independently drifting literal.
    assert update_cmd_zip._zip_archive_url("main").startswith(
        update_cmd_git.OFFICIAL_REPO_URL.removesuffix(".git")
    )

    source = _read("hermes_cli/update_cmd_zip.py")
    assert "NousResearch/hermes-agent" not in source
    assert "hermes-agent.nousresearch.com" not in source


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


def test_model_catalog_defaults_belong_to_stardust() -> None:
    from hermes_cli import model_catalog
    from hermes_cli.config import DEFAULT_CONFIG

    canonical = (
        "https://raw.githubusercontent.com/9529360-cpu/stardust-hermes"
        "/main/website/static/api/model-catalog.json"
    )
    assert model_catalog.DEFAULT_CATALOG_URL == canonical
    assert DEFAULT_CONFIG["model_catalog"]["url"] == canonical

    forbidden = (
        "https://hermes-agent.nousresearch.com/docs/api/model-catalog.json",
        (
            "https://raw.githubusercontent.com/NousResearch/hermes-agent"
            "/main/website/static/api/model-catalog.json"
        ),
    )
    for path in ("hermes_cli/model_catalog.py", "hermes_cli/config_defaults.py"):
        source = _read(path)
        for old in forbidden:
            assert old not in source


def test_plugin_catalog_defaults_belong_to_stardust() -> None:
    from hermes_cli import plugin_catalog

    canonical = (
        "https://raw.githubusercontent.com/9529360-cpu/stardust-hermes"
        "/main/website/static/api/plugin-catalog.json"
    )
    assert plugin_catalog.LIVE_CATALOG_URL == canonical

    source = _read("hermes_cli/plugin_catalog.py")
    assert (
        "https://hermes-agent.nousresearch.com/docs/api/plugin-catalog.json"
        not in source
    )
    assert '"plugin-catalog-stardust-v1.json"' in source


def test_skills_index_defaults_belong_to_stardust() -> None:
    from tools import skills_hub_search

    canonical = (
        "https://github.com/9529360-cpu/stardust-hermes"
        "/releases/download/stardust-skills-index/skills-index.json"
    )
    assert skills_hub_search.HERMES_INDEX_URL == canonical
    assert skills_hub_search._hermes_index_cache_file().name == (
        "stardust-skills-index-v1.json"
    )

    old = "https://hermes-agent.nousresearch.com/docs/api/skills-index.json"
    for path in ("tools/skills_hub_search.py", "website/scripts/prebuild.mjs"):
        source = _read(path)
        assert old not in source
        assert "9529360-cpu/stardust-hermes" in source
        assert "stardust-skills-index" in source

