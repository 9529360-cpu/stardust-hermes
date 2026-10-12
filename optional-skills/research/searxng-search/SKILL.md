---
name: searxng-search
description: Free keyless meta-search aggregating 70+ engines.
version: 1.0.1
author: hermes-agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [search, searxng, meta-search, self-hosted, free, fallback]
    related_skills: [duckduckgo-search, domain-intel]
    fallback_for_toolsets: [web]
---

# SearXNG Search

Meta-search through a SearXNG instance, with no API key. The `web-searxng` provider plugin does the searching, so use `web_search` for ordinary queries:

- Manifest: `plugins/web/searxng/plugin.yaml` (`provides_web_providers: [searxng]`, "Requires SEARXNG_URL").
- Implementation: `plugins/web/searxng/provider.py`. It calls `GET {SEARXNG_URL}/search?q=...&format=json&pageno=1`.

## Setup

- Environment variable: `SEARXNG_URL`, the base URL of the instance, for example `http://localhost:8888` for a self-hosted instance. Without it the provider returns "SEARXNG_URL is not set", and this search is unavailable.
- Self-host with Docker: `docker run -d -p 8888:8080 -v $(pwd)/searxng:/etc/searxng searxng/searxng:latest`, then set `SEARXNG_URL=http://localhost:8888`.
- Self-host with pip: `pip install searxng`, edit `/etc/searxng/settings.yml`, then run `searxng-run`.
- Public instances are listed at https://searxng.org/. They may rate-limit or block queries.

Check that an instance answers with `curl -s --max-time 5 "${SEARXNG_URL}/search?q=test&format=json" | head -c 200`.

## Query parameters

The plugin sends only `q`, `format=json`, and `pageno=1`. Engine, category, safesearch, and time-range parameters are in `references/query-parameters.md`, for use with `curl` through `terminal`.

## Troubleshooting

| Problem | Likely cause | What to do |
|---------|--------------|------------|
| `SEARXNG_URL` not set | No instance configured | Use a public SearXNG instance or set up your own |
| Connection refused | Instance not running, or wrong URL | Check the URL and that the instance is running |
| Empty results | Instance blocks the query | Try a different instance or self-host |
| Slow responses | Public instance under load | Self-host, or use a less-loaded public instance |
| `json` format not supported | Old SearXNG version | Try `format=rss` or upgrade SearXNG |

## Pitfalls

- Always set `SEARXNG_URL`. Without it the provider cannot search.
- Self-hosting is the most reliable option. Public instances may go down, rate-limit, or block.
