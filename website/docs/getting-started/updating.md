---
sidebar_position: 3
title: "Updating & Uninstalling"
description: "How Stardust source refresh, rollback, and uninstall work"
---

# Updating & Uninstalling

## Updating Stardust

Stardust keeps the inherited `hermes update` command name for compatibility, but the public CLI updater is currently **pinned off**. Running `hermes update` only reports that policy; it does not fetch, pull, reinstall dependencies, restart gateways, or replace the checkout. Legacy update flags such as `--check`, `--plan`, and `--backup` are still accepted so older launchers and scripts do not fail argument parsing, but they do not enable the pinned updater.

### Installer-managed installations

For an installation created by the Stardust installer, refresh it by re-running the Stardust-owned bootstrap. The maintained installer detects an existing checkout and uses its existing-install path while keeping product source authority on `9529360-cpu/stardust-hermes`.

Linux / macOS / WSL2 / Android (Termux):

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

Windows PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

Do not substitute the former Hermes website installer or an upstream repository URL. Stardust's bootstrap wrappers rewrite and validate product-source URLs before invoking the inherited installer machinery.

### Passive update notices

Passive CLI/banner update comparison is off by default:

```yaml
updates:
  check: false
```

To opt in to passive comparison only:

```bash
hermes config set updates.check true
```

This setting does not enable `hermes update`, does not authorize a source mutation, and does not change the Desktop client's separate update behavior.

### Manual source checkouts

If you deliberately maintain a source checkout yourself, update it as a normal Git checkout rather than relying on the pinned CLI updater. Inspect and preserve local work first, then use your normal reviewed Git workflow. A typical clean-main refresh is:

```bash
git status --short
git fetch origin
git checkout main
git pull --ff-only origin main
source .venv/bin/activate 2>/dev/null || source venv/bin/activate
uv pip install -e ".[all]"
hermes config check
hermes doctor
```

Do not use `git reset --hard`, `git clean`, or other destructive recovery commands as a routine update step. Source rollback and user-data recovery are separate concerns.

### Desktop updates

Stardust Desktop owns client application updates. Its product update affordances must not reactivate the inherited backend `hermes update` synchronization path or replace the backend checkout from another repository.

### Rollback and recovery

Before a manual source rollback, protect the profile data you care about. Code rollback does not automatically roll back configuration, databases, memories, or other durable state.

For a manually maintained checkout, a source-only rollback can use a reviewed known-good commit and then reinstall dependencies:

```bash
git log --oneline -10
git checkout <known-good-commit>
uv pip install -e ".[all]"
hermes config check
hermes doctor
```

Return to `main` with your normal Git workflow after the issue is resolved. If durable state also needs recovery, use the relevant Stardust backup/snapshot recovery path rather than assuming a Git checkout restores user data.

### Checking the current version

```bash
hermes --version
```

Repository history and current source live at `9529360-cpu/stardust-hermes`; Stardust does not use upstream Hermes releases as its product update authority.

---
## Uninstalling

```bash
hermes uninstall
```

The uninstaller gives you the option to keep your configuration files (`~/.hermes/`) for a future reinstall.

:::tip Moving to a new machine rather than leaving?
Take your setup with you before removing anything: `hermes backup` captures the entire `~/.hermes` directory including credentials, while `hermes profile export` packs a single profile with credentials excluded by design (so an export alone is not a full backup). See [`hermes backup` vs `hermes profile export`](/reference/faq#hermes-backup-vs-hermes-profile-export).
:::

### Manual Uninstall

```bash
rm -f ~/.local/bin/hermes
rm -rf /path/to/hermes-agent
rm -rf ~/.hermes            # Optional — keep if you plan to reinstall
```

:::info
If you installed the gateway as a system service, stop and disable it first:
```bash
hermes gateway stop
# Linux: systemctl --user disable hermes-gateway
# macOS: launchctl remove ai.hermes.gateway
```
:::
