# DuckDuckGo media search (ddgs CLI and Python API)

Moved here from the former `duckduckgo-search` SKILL.md. The `web-ddgs` plugin covers text search only. The commands below cover the other `ddgs` search types. Install the package first (`pip install ddgs`) in the runtime that will run them.

## CLI

Run with `terminal`:

```bash
# News search
ddgs news -q "artificial intelligence" -m 5

# Image search
ddgs images -q "landscape photography" -m 10

# Video search
ddgs videos -q "python tutorial" -m 5
```

### CLI flags

| Flag | Description | Example |
|------|-------------|---------|
| `-q` | Query (required) | `-q "search terms"` |
| `-m` | Max results | `-m 5` |
| `-r` | Region | `-r us-en` |
| `-t` | Time limit (`d`, `w`, `m`, `y`) | `-t w` (week) |
| `-s` | Safe search | `-s off` |
| `-o` | Output format | `-o json` |

## Python API

Use `DDGS` only after confirming that `ddgs` imports in that runtime. Pass `max_results` as a keyword argument; positional use raises an error on all methods.

### News

Best for: current events, breaking news, latest updates.

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.news("AI regulation 2026", max_results=5):
        print(r["date"], "-", r["title"])
        print(r.get("source", ""), "|", r["url"])
        print(r.get("body", "")[:200])
        print()
```

Returns: `date`, `title`, `body`, `url`, `image`, `source`

### Images

Best for: visual references, product images, diagrams.

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.images("semiconductor chip", max_results=5):
        print(r["title"])
        print(r["image"])
        print(r.get("thumbnail", ""))
        print(r.get("source", ""))
        print()
```

Returns: `title`, `image`, `thumbnail`, `url`, `height`, `width`, `source`

### Videos

Best for: tutorials, demos, explainers.

```python
from ddgs import DDGS

with DDGS() as ddgs:
    for r in ddgs.videos("FastAPI tutorial", max_results=5):
        print(r["title"])
        print(r.get("content", ""))
        print(r.get("duration", ""))
        print(r.get("provider", ""))
        print(r.get("published", ""))
        print()
```

Returns: `title`, `content`, `description`, `duration`, `provider`, `published`, `statistics`, `uploader`

## Limitations

- **Rate limiting**: DuckDuckGo may throttle after many rapid requests. Add a short delay between searches if needed.
- **Availability**: DuckDuckGo may block some cloud IPs. If searches return empty, try different keywords or wait a few seconds.
- **Field variability**: Return fields may vary between results or `ddgs` versions. Use `.get()` for optional fields to avoid `KeyError`.
- **Separate runtimes**: A successful `ddgs` install in the terminal does not mean `execute_code` can import it.

## Troubleshooting

| Problem | Likely cause | What to do |
|---------|--------------|------------|
| `ddgs: command not found` | CLI not installed in the shell environment | Install `ddgs`, or use built-in web/browser tools instead |
| `ModuleNotFoundError: No module named 'ddgs'` | Python runtime does not have the package | Do not use Python DDGS there until that runtime is prepared |
| Search returns nothing | Temporary rate limiting or poor query | Wait a few seconds, retry, or adjust the query |
| CLI works but `execute_code` import fails | Terminal and `execute_code` are different runtimes | Keep using the CLI, or prepare the Python runtime separately |
