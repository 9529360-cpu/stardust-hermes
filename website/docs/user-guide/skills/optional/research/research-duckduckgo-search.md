---
title: "Duckduckgo Search — Free keyless web, news, and image search via ddgs"
sidebar_label: "Duckduckgo Search"
description: "Free keyless web, news, and image search via ddgs"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Duckduckgo Search

Free keyless web, news, and image search via ddgs.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/research/duckduckgo-search` |
| Path | `optional-skills/research\duckduckgo-search` |
| Version | `1.3.0` |
| Author | gamedevCloudy |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `search`, `duckduckgo`, `web-search`, `free`, `fallback` |
| Related skills | [`arxiv`](/docs/user-guide/skills/bundled/research/research-arxiv) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# DuckDuckGo Search

Free DuckDuckGo search with no API key. Text search is the `web-ddgs` provider plugin, so use `web_search` for ordinary queries:

- Manifest: `plugins/web/ddgs/plugin.yaml` (`provides_web_providers: [ddgs]`, "no API key required").
- Implementation: `plugins/web/ddgs/provider.py`. It calls `DDGS(timeout=...).text(query, max_results=...)` in a bounded subprocess worker.

## Setup

- Install with `pip install ddgs`. The package provides the Python `ddgs` module that the plugin imports, and the `ddgs` CLI.
- Verify the CLI with `ddgs --help`. Verify Python in the runtime that will run the search with `from ddgs import DDGS`.
- The `terminal` tool and `execute_code` are separate runtimes. Installing `ddgs` in one does not install it in the other.

## News, images, and video

The plugin covers text search only. For `ddgs news`, `ddgs images`, and `ddgs videos`, read `references/media-search.md`.

## Pitfalls

- If `ddgs` returns nothing, DuckDuckGo may be rate-limiting. Wait a few seconds and retry, or change the query.
- Do not assume `ddgs` exists in every runtime. Check it first.
