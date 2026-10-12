# SearXNG query parameters

Moved here from the former `searxng-search` SKILL.md. The `web-searxng` plugin sends only `q`, `format=json`, and `pageno=1`. The parameters below come from the original skill and are not exposed by the plugin. They were not re-verified against a live instance in this change, so check them on your instance before relying on them.

## Curl examples

Run with `terminal`:

```bash
# Text search (JSON output)
curl -s --max-time 10 \
  "${SEARXNG_URL}/search?q=python+async+programming&format=json&engines=google,bing&limit=10"

# With safesearch off
curl -s --max-time 10 \
  "${SEARXNG_URL}/search?q=example&format=json&safesearch=0"

# Specific categories (general, news, science, etc.)
curl -s --max-time 10 \
  "${SEARXNG_URL}/search?q=AI+news&format=json&categories=news"
```

## Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `q` | Query string (URL-encoded) | `q=python+async` |
| `format` | Output format: `json`, `csv`, `rss` | `format=json` |
| `engines` | Comma-separated engine names | `engines=google,bing,ddg` |
| `limit` | Max results per engine (default 10) | `limit=5` |
| `categories` | Filter by category | `categories=news,science` |
| `safesearch` | 0=none, 1=moderate, 2=strict | `safesearch=0` |
| `time_range` | Filter: `day`, `week`, `month`, `year` | `time_range=week` |

## Parsing JSON results

```bash
curl -s --max-time 10 "${SEARXNG_URL}/search?q=fastapi&format=json&limit=5" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data.get('results', []):
    print(r.get('title',''))
    print(r.get('url',''))
    print(r.get('content','')[:200])
    print()
"
```

Returns per result: `title`, `url`, `content` (snippet), `engine`, `parsed_url`, `img_src`, `thumbnail`, `author`, `published_date`.

## Workflow: search then extract

SearXNG returns titles, URLs, and snippets, not full page content. To get the page content, search first, then extract the most relevant URL with `web_extract`, the browser tools, or `curl`.
