---
sidebar_position: 2
title: "Installation"
description: "Install Stardust on Linux, macOS, WSL2, native Windows, or Android via Termux"
---

# Installation

Stardust is maintained and distributed from `9529360-cpu/stardust-hermes`. Fresh installs must use the Stardust-owned bootstrap scripts below rather than the former Hermes website installer.

:::tip Platform Support
For the full platform support matrix (which OSes, distribution methods, and
platform-gated features are supported), see **[Platform Support](./platform-support.md)**.
:::

## Quick Install

### Linux / macOS / WSL2 / Android (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

### Windows (native)

Run in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

The inherited CLI command remains `hermes` for compatibility. After a command-line install, launch the Stardust desktop surface with:

```bash
hermes desktop
```

### What the Installer Does

The Stardust bootstrap downloads installer logic from this repository, pins the selected source ref when requested, rewrites inherited product-source/recovery URLs to Stardust, and fails closed if an upstream Hermes product source survives that boundary. The underlying installer then handles dependencies (Python, Node.js, ripgrep, ffmpeg), the repo clone, virtual environment, `hermes` command setup, and provider configuration.

#### Install Layout

Stardust deliberately keeps the inherited runtime/data layout so an existing Hermes-based installation can remain compatible while the product evolves:

| Installer                              | Code lives at                  | `hermes` binary                         | Data directory                       |
| -------------------------------------- | ------------------------------ | --------------------------------------- | ------------------------------------ |
| Per-user (git installer)               | `~/.hermes/hermes-agent/`      | `~/.local/bin/hermes` (symlink)         | `~/.hermes/`                         |
| Root-mode (`sudo curl … \| sudo bash`) | `/usr/local/lib/hermes-agent/` | `/usr/local/bin/hermes`                 | `/root/.hermes/` (or `$HERMES_HOME`) |

Those names are compatibility interfaces, not current product branding. Renaming them without a migration would risk existing config, sessions, launchers, services, and desktop installs.

The root-mode FHS layout (`/usr/local/lib/…`, `/usr/local/bin/hermes`) is useful for shared-machine deployments where one system install serves every user. Per-user config, auth, skills, and sessions still live under each user's `~/.hermes/` or explicit `HERMES_HOME`.

### After Installation

Reload your shell and start chatting:

```bash
source ~/.bashrc   # or: source ~/.zshrc
hermes             # Start the compatible Stardust CLI runtime
```

To reconfigure individual settings later:

```bash
hermes model          # Choose your LLM provider and model
hermes tools          # Configure which tools are enabled
hermes gateway setup  # Set up messaging platforms
hermes config set     # Set individual config values
hermes config get     # Inspect individual config values
hermes setup          # Run the full setup wizard
```

Optional provider integrations documented elsewhere in this site, including Nous Portal, remain available where they are useful. They are service choices, not the source or update authority for Stardust.

:::tip Moving an existing setup
You do not need to rebuild your setup from scratch. Restore a full backup with `hermes import` (see [Exporting Hermes-compatible data to another machine](/reference/faq#exporting-hermes-to-another-machine)), or bring over a single profile with `hermes profile import`. Profile exports exclude credentials by design, so an export alone is not a full backup.
:::

---

## Prerequisites

**Installer:** On non-Windows platforms, the primary prerequisite is **Git**. On Linux, also make sure `curl` and `xz-utils` are available. The desktop app additionally requires `g++` (or `build-essential` on Debian/Ubuntu) to compile native modules. The installer handles the rest:

- **uv** (Python package manager)
- **Python 3.11**
- **Node.js v26** (existing compatible system Node is reused when appropriate)
- **ripgrep**
- **ffmpeg**

:::info
You do **not** need to install Python, Node.js, ripgrep, or ffmpeg manually. Make sure `git` is available (`git --version`). On Debian/Ubuntu, ensure `curl`, `xz-utils`, and—when building the desktop—`build-essential` are installed.
:::

:::tip Nix users
Nix is a best-effort compatibility path rather than Stardust's primary installation route. Existing Nix users can still use the inherited flake/module documentation in **[Nix & NixOS Setup](./nix-setup.md)**.
:::

---

## Manual / Developer Installation

If you want a source checkout for development or to run a specific branch/ref, see the [Development Setup](../developer-guide/contributing.md#development-setup) section. Any clone, recovery URL, or bootstrap command for Stardust should ultimately resolve to `9529360-cpu/stardust-hermes`.

---

## Non-Sudo / System Service User Installs

Running Stardust as a dedicated unprivileged user is supported. The Playwright `--with-deps` step is the part most likely to require root because it installs system libraries for Chromium. The installer detects whether sudo is available and can continue without that system-library step when necessary.

**Recommended split (Debian/Ubuntu):**

1. **One time, as an admin user with sudo**, install the system libraries Chromium needs:

   ```bash
   sudo npx playwright install-deps chromium
   ```

2. **As the unprivileged service user**, run the Stardust installer:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
   ```

   To skip browser automation entirely:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash -s -- --skip-browser
   ```

   The installer also pre-installs `cua-driver` for Computer Use; pass `--skip-computer-use` to opt out.

3. **Make `hermes` available to the service user's shells.** The compatible launcher is written to `~/.local/bin/hermes`. System service accounts often have a minimal PATH, so either add that directory or symlink the launcher into a system location:

   ```bash
   # Option A — add to the service user's profile
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

   # Option B — symlink system-wide (run as an admin)
   sudo ln -s /home/hermes/.hermes/hermes-agent/venv/bin/hermes /usr/local/bin/hermes
   ```

4. **Verify:** `hermes doctor` should run cleanly. If you get `ModuleNotFoundError: No module named 'dotenv'`, you are likely invoking the checkout's source wrapper with system Python instead of the venv launcher.

5. **Running the messaging gateway from this account?** Enable lingering if you need a user service to survive logout and start at boot:

   ```bash
   sudo loginctl enable-linger <service-user>
   ```

   See [Messaging Gateway](/user-guide/messaging/) for service setup.

The same pattern works on other supported/best-effort Linux distributions; system library installation is distro-specific and the installer prints the relevant guidance.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `hermes: command not found` | Reload your shell (`source ~/.bashrc`) or check PATH |
| `API key not set` | Run `hermes model` to configure your provider, or `hermes config set OPENROUTER_API_KEY your_key` |
| Missing config after a code change | Run `hermes config check` then `hermes config migrate` |

For more diagnostics, run `hermes doctor`.

### Symlinked home directories and external storage

The inherited runtime supports a symlinked `HERMES_HOME` and symlinked home subdirectories, including `hooks`, `skills`, `sessions`, and `logs`. During home initialization, existing directory links are preserved and permissions on linked directories are left to their owner.

If a link target is missing, inaccessible, or not a directory, initialization stops with a storage error naming the path and link target. Stardust does not replace the link or create its missing target because doing so could write data onto the local disk while an external or NAS volume is unmounted. Restore the intended mount/target and verify permissions before retrying.

`hermes doctor` reports these failures as storage problems rather than invalid YAML. Keep the existing `config.yaml`; setup is not the repair for an unavailable directory.

## Update policy

Stardust does **not** use the inherited upstream `hermes update` flow as its product update mechanism. That entrypoint is intentionally disabled so an independently maintained checkout cannot be silently synchronized back to Hermes upstream.

- Stardust Desktop product update actions update the Stardust client itself.
- Repository/source updates are maintained and released from `9529360-cpu/stardust-hermes`.
- Docker/Nix/external-package deployments remain externally managed according to their deployment model.
- If a repair or reinstall is required, use the Stardust-owned installer commands at the top of this page.
