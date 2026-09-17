---
sidebar_position: 1
title: "Stardust Quickstart"
description: "Install Stardust, choose a model provider, verify a first chat, and launch the personal-assistant desktop"
---

# Stardust Quickstart

This is the shortest supported path from a clean machine to a working Stardust assistant. Stardust keeps the inherited `hermes` command for runtime compatibility, but installation, updates, recovery, and ongoing maintenance belong to `9529360-cpu/stardust-hermes`.

## 1. Install Stardust

### Linux / macOS / WSL2 / Android (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

### Windows PowerShell

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

Do not use the former Hermes website installer for Stardust. The Stardust bootstrap downloads installer logic from this repository, keeps recovery/source URLs on this repository, and refuses to run if an upstream Hermes product source survives the bootstrap boundary.

For platform-specific details, see [Installation](./installation.md) and [Android / Termux](./termux.md).

## 2. Choose a model provider

Run the inherited configuration command:

```bash
hermes model
```

Pick the provider you actually intend to use. Stardust supports the inherited provider ecosystem, including direct API providers, OpenAI-compatible endpoints, local/self-hosted endpoints, subscription/OAuth providers, and optional services such as Nous Portal.

Provider choice does **not** change Stardust's source authority. A Nous, OpenAI, Anthropic, OpenRouter, local, or other provider is a model service; it is not the repository/update source for Stardust.

If you prefer the full setup wizard:

```bash
hermes setup
```

Secrets remain under the profile-aware `HERMES_HOME` data hierarchy rather than the Git checkout. Never commit `.env`, API keys, tokens, profile exports containing private data, conversations, or local databases to this repository.

## 3. Verify a first conversation

Start the compatible CLI runtime:

```bash
hermes
```

Or use the terminal UI:

```bash
hermes --tui
```

Use a prompt with an observable result, for example:

```text
Summarize the current repository in five bullets and identify its main entry points.
```

A healthy baseline is simple:

- the selected model/provider appears correctly;
- the assistant answers without a provider/configuration error;
- terminal/file tools work when requested and permitted;
- a second turn continues the same conversation normally.

If the base chat is broken, fix that before layering on gateway, cron, skills, plugins, voice, or complex fallback routing.

## 4. Verify session resume

```bash
hermes --continue
```

Resume should reopen the latest session and retain the conversation context. The runtime still uses inherited Hermes session/config paths for compatibility; Stardust does not rename user-data directories without an explicit migration.

## 5. Launch Stardust Desktop

```bash
hermes desktop
```

The desktop is the primary personal-assistant workbench in this repository. It can use a local runtime or an explicitly configured remote Gateway. Product-facing update actions belong to the Stardust client and must not reactivate the inherited upstream `hermes update` synchronization path.

For desktop development and architecture, see the repository's [`apps/desktop/README.md`](https://github.com/9529360-cpu/stardust-hermes/blob/main/apps/desktop/README.md).

## 6. Optional capabilities

Once the base assistant is healthy, add only the capabilities you need:

```bash
hermes tools
hermes gateway setup
hermes config get
hermes config set
```

Useful next reads:

- [Configuration](/user-guide/configuration)
- [Configuring Models](/user-guide/configuring-models)
- [Messaging](/user-guide/messaging)
- [Tools](/user-guide/features/tools)
- [Skills](/user-guide/features/skills)
- [Memory](/user-guide/features/memory)
- [Provider Routing](/user-guide/features/provider-routing)
- [Credential Pools](/user-guide/features/credential-pools)

## Troubleshooting

Start with:

```bash
hermes doctor
```

Common checks:

- reload the shell if `hermes` is not on `PATH`;
- re-run `hermes model` if provider credentials/model selection are wrong;
- verify local/self-hosted endpoints are reachable from the machine running the backend;
- remember that in remote mode tools execute on the Gateway host, not necessarily the computer displaying Stardust Desktop.

If a reinstall is required, use the Stardust bootstrap commands at the top of this page. Do not use an upstream Hermes installer to repair an independently maintained Stardust checkout.

## Update policy

Stardust does not track Hermes releases through periodic merge/rebase or automatic upstream synchronization. The inherited `hermes update` source-sync path is intentionally disabled for the Stardust edition.

Source, release, install, recovery, and maintenance authority: **[9529360-cpu/stardust-hermes](https://github.com/9529360-cpu/stardust-hermes)**.

Hermes Agent remains the open-source technical foundation and historical code origin; Stardust is the independently maintained personal-assistant project built from that foundation.
