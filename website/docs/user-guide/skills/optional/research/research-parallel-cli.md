---
title: "Parallel Cli — Agent-native web search, deep research, and enrichment"
sidebar_label: "Parallel Cli"
description: "Agent-native web search, deep research, and enrichment"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Parallel Cli

Agent-native web search, deep research, and enrichment.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/research/parallel-cli` |
| Path | `optional-skills/research\parallel-cli` |
| Version | `1.1.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Research`, `Web`, `Search`, `Deep-Research`, `Enrichment`, `CLI` |
| Related skills | [`duckduckgo-search`](/docs/user-guide/skills/optional/research/research-duckduckgo-search), [`mcporter`](/docs/user-guide/skills/optional/mcp/mcp-mcporter) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Parallel CLI

Use `parallel-cli` when the user explicitly wants Parallel, or when a terminal-native workflow needs Parallel's deep research, enrichment, FindAll, or monitoring. This is an optional third-party workflow, not a Hermes core capability. Parallel is a paid service with a free tier.

Web search and extraction through Hermes are not this CLI. They are the `web-parallel` provider plugin, which `web_search` and `web_extract` use:

- Manifest: `plugins/web/parallel/plugin.yaml` (`provides_web_providers: [parallel]`).
- Implementation: `plugins/web/parallel/provider.py`. It needs `PARALLEL_API_KEY`. `PARALLEL_SEARCH_MODE` selects the search mode (default `agentic`).

For ordinary lookups, prefer `web_search` and `web_extract`. Use this CLI when the user asks for Parallel, or when the work needs the CLI-only search filters, research, enrichment, FindAll, or monitoring in `references/cli-workflows.md`.

## Installation

Try the least invasive install path available for the environment.

### Homebrew

```bash
brew install parallel-web/tap/parallel-cli
```

### npm

```bash
npm install -g parallel-web-cli
```

### Python package

```bash
pip install "parallel-web-tools[cli]"
```

### Standalone installer

```bash
curl -fsSL https://parallel.ai/install.sh | bash
```

If you want an isolated Python install, `pipx` can also work:

```bash
pipx install "parallel-web-tools[cli]"
pipx ensurepath
```

## Authentication

Interactive login:

```bash
parallel-cli login
```

Headless / SSH / CI:

```bash
parallel-cli login --device
```

API key environment variable:

```bash
export PARALLEL_API_KEY="***"
```

Verify current auth status:

```bash
parallel-cli auth
```

If auth requires browser interaction, run with `pty=true`.

## Error handling and exit codes

The CLI documents these exit codes:
- `0` success
- `2` bad input
- `3` auth error
- `4` API error
- `5` timeout

If you hit auth errors:
1. check `parallel-cli auth`
2. confirm `PARALLEL_API_KEY` or run `parallel-cli login` / `parallel-cli login --device`
3. verify `parallel-cli` is on `PATH`
