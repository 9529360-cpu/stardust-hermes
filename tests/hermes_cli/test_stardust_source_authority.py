from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STARDUST_INSTALL_BASE = "raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust"
UPSTREAM_INSTALL_HOST = "hermes-agent.nousresearch.com/install."
STARDUST_DOCS_BASE = "https://github.com/9529360-cpu/stardust-hermes/blob/main/website/docs"
UPSTREAM_DOCS_HOST = "hermes-agent.nousresearch.com/docs"


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


def test_cli_docs_urls_point_to_stardust_repository() -> None:
    """"Learn more"/help-text doc links (DOCS_URL-style constants and inline print/help
    strings) must resolve to a real page. There's no live Stardust docs domain yet, so these
    point at the GitHub-rendered markdown source under website/docs/ instead of guessing at
    a domain that doesn't serve anything."""
    docs_link_files = (
        "hermes_cli/auth_codex.py",
        "hermes_cli/auth_constants.py",
        "hermes_cli/dashboard_auth/login_page.py",
        "hermes_cli/fallback_cmd.py",
        "hermes_cli/kanban_parser.py",
        "hermes_cli/main_dashboard.py",
        "hermes_cli/portal_cli.py",
        "hermes_cli/setup.py",
        "hermes_cli/setup_platforms.py",
        "hermes_cli/setup_whatsapp_cloud.py",
        "hermes_cli/subcommands/egress.py",
        "hermes_cli/subcommands/fallback.py",
        "hermes_cli/subcommands/secrets.py",
        "hermes_cli/subcommands/worktree.py",
        "hermes_cli/tools_config.py",
        "hermes_cli/update_cmd_maint.py",
        "hermes_cli/web_server_oauth.py",
        "plugins/platforms/discord/adapter.py",
        "plugins/platforms/slack/adapter.py",
        "setup.py",
    )
    for path in docs_link_files:
        source = _read(path)
        assert STARDUST_DOCS_BASE in source, f"{path} has no Stardust docs link"
        assert UPSTREAM_DOCS_HOST not in source, f"{path} still links the upstream docs domain"

    # Every referenced page actually exists in this repository's docs tree, so the link resolves.
    referenced_pages = {
        "integrations/providers.md": ("hermes_cli/auth_codex.py", "hermes_cli/setup.py"),
        "user-guide/features/spotify.md": ("hermes_cli/auth_constants.py",),
        "guides/oauth-over-ssh.md": ("hermes_cli/auth_constants.py", "tools/mcp_oauth.py"),
        "user-guide/features/web-dashboard.md": (
            "hermes_cli/dashboard_auth/login_page.py", "hermes_cli/main_dashboard.py",
        ),
        "user-guide/features/fallback-providers.md": (
            "hermes_cli/fallback_cmd.py", "hermes_cli/subcommands/fallback.py",
        ),
        "user-guide/features/kanban.md": ("hermes_cli/kanban_parser.py",),
        "user-guide/features/tool-gateway.md": ("hermes_cli/portal_cli.py",),
        "user-guide/configuration.md": ("hermes_cli/setup.py",),
        "user-guide/messaging/webhooks.md": (
            "hermes_cli/setup_platforms.py", "hermes_cli/web_server_messaging.py",
        ),
        "user-guide/messaging/whatsapp-cloud.md": (
            "hermes_cli/web_server_messaging.py",
        ),
        "user-guide/egress/iron-proxy.md": ("hermes_cli/subcommands/egress.py",),
        "user-guide/secrets/index.md": ("hermes_cli/subcommands/secrets.py",),
        "user-guide/cli.md": ("hermes_cli/subcommands/worktree.py",),
        "user-guide/features/tools.md": ("hermes_cli/tools_config.py",),
        "user-guide/features/curator.md": ("hermes_cli/update_cmd_maint.py",),
        "guides/xai-grok-oauth.md": ("hermes_cli/web_server_oauth.py",),
        "user-guide/messaging/discord.md": ("plugins/platforms/discord/adapter.py",),
        "user-guide/messaging/slack.md": ("plugins/platforms/slack/adapter.py",),
        "getting-started/installation.md": ("setup.py",),
    }
    for relative_page, referencing_files in referenced_pages.items():
        assert (ROOT / "website" / "docs" / relative_page).is_file(), (
            f"{relative_page} is referenced by {referencing_files} but doesn't exist")
        for path in referencing_files:
            source = _read(path)
            assert relative_page in source, f"{path} does not reference {relative_page}"

    # Wrapped across two print-string literals for terminal width; check the unsplit tail.
    assert "messaging/whatsapp-cloud.md" in _read("hermes_cli/setup_whatsapp_cloud.py")


def test_messaging_platform_catalog_docs_urls_point_to_stardust_repository() -> None:
    source = _read("hermes_cli/web_server_messaging.py")
    assert UPSTREAM_DOCS_HOST not in source

    for relative_page in (
        "user-guide/messaging/index.md",
        "user-guide/messaging/google_chat.md",
        "user-guide/messaging/weixin.md",
        "user-guide/messaging/teams.md",
        "user-guide/messaging/irc.md",
        "user-guide/messaging/line.md",
        "user-guide/messaging/ntfy.md",
        "user-guide/messaging/photon.md",
        "user-guide/messaging/raft.md",
        "user-guide/messaging/simplex.md",
        "user-guide/messaging/webhooks.md",
        "user-guide/messaging/msgraph-webhook.md",
        "user-guide/messaging/whatsapp-cloud.md",
    ):
        assert (ROOT / "website" / "docs" / relative_page).is_file()
        assert f"{STARDUST_DOCS_BASE}/{relative_page}" in source

    # The Telegram onboarding pairing service is a real Nous-operated backend the bot setup
    # flow depends on, not a docs link — it stays on the upstream host until Stardust stands
    # up its own onboarding API.
    assert '_TELEGRAM_ONBOARDING_DEFAULT_URL = "https://setup.hermes-agent.nousresearch.com"' in source


def test_telegram_managed_bot_onboarding_service_is_not_a_docs_link() -> None:
    """DEFAULT_API_URL is a real pairing API the Telegram onboarding client calls, distinct
    from the DOCS_URL-style links this migration covers. Leave it on the real backend."""
    source = _read("hermes_cli/telegram_managed_bot.py")
    assert 'DEFAULT_API_URL = "https://setup.hermes-agent.nousresearch.com"' in source


def test_model_catalog_migration_keeps_historical_marker_url() -> None:
    """The v45->v46 config migration matches this exact historical URL to detect and rewrite
    a stale default; it is a detection key, not a live link, and must not be touched."""
    source = _read("hermes_cli/config_migrations.py")
    assert 'old="https://hermes-agent.nousresearch.com/docs/api/model-catalog.json"' in source


def test_cimd_client_metadata_comment_documents_real_upstream_redirect() -> None:
    """This comment records why the OAuth CIMD client-metadata document is hosted on
    nousresearch.github.io instead of the docs domain (which 301s and breaks CIMD fetchers
    that must not follow redirects) -- historical/technical context, not a dead link to fix."""
    source = _read("tools/mcp_oauth.py")
    assert "hermes-agent.nousresearch.com/docs/* 301s here" in source
    assert '_CIMD_CLIENT_METADATA_URL = "https://nousresearch.github.io/hermes-agent/docs/oauth/client-metadata.json"' in source


def test_openrouter_referer_headers_are_attribution_not_doc_links() -> None:
    """HTTP-Referer/X-Title app-identification headers sent to OpenRouter-compatible APIs are
    attribution, not a "learn more" doc link -- out of scope for the docs-link migration."""
    for path in (
        "agent/anthropic_adapter.py",
        "agent/auxiliary_client.py",
        "hermes_cli/models.py",
        "plugins/model-providers/ai-gateway/__init__.py",
        "plugins/model-providers/fireworks/__init__.py",
        "plugins/model-providers/kimi-coding/__init__.py",
        "plugins/model-providers/opencode-free/__init__.py",
        "plugins/model-providers/opencode-zen/__init__.py",
        "plugins/web/perplexity/provider.py",
    ):
        source = _read(path)
        assert 'HTTP-Referer": "https://hermes-agent.nousresearch.com"' in source

