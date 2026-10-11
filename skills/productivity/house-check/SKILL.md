---
name: house-check
description: Read-only report of disk space, caches, and Stardust data.
version: 1.0.0
author: 阿豪 (9529360-cpu)
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [disk, storage, cache, cleanup, status, system, windows]
    category: productivity
---

# House Check Skill

Reports the state of the owner's computer: free space on each drive, the size of known package and browser caches, and the size of the Stardust data home. It only reports. It never deletes, moves, or edits anything, and it never runs the cleanup commands it suggests.

## When to Use

- The owner asks how the computer is doing or how much space is left: "电脑现在怎么样", "C 盘还剩多少", "家里状态".
- A morning or daily brief needs a computer-status section. Run the script and include its 磁盘 section.
- Before a large download, install, or model pull, to check free space.
- Not for: deleting, moving, or editing caches or files (the owner does those), CPU, memory, or process checks, or network checks.

## Prerequisites

- The repository Python environment (`.venv`, Python 3.11 or newer). The script uses only the standard library, so nothing needs installing.
- Run it with the `terminal` tool from the repository root. On Windows it checks every drive letter that exists; on Linux and macOS it checks `/`.
- The Stardust data home comes from the runtime's own resolver when the script runs inside the repository. A copy outside the repository follows the same rules: `STARDUST_HOME` when set, else the platform default home.

## How to Run

```bash
python skills/productivity/house-check/scripts/house_check.py
python skills/productivity/house-check/scripts/house_check.py --json
python skills/productivity/house-check/scripts/house_check.py --max-seconds 30 --max-entries 500000
```

## Quick Reference

| Need | Command |
| --- | --- |
| Report for the owner (Chinese Markdown, three sections) | `house_check.py` |
| The same data for another tool | `house_check.py --json` |
| Complete totals for large caches | raise `--max-seconds` and `--max-entries` |

The three Markdown sections are `## 磁盘` (drives), `## 缓存与数据` (the Stardust data home and the known caches), and `## 可以考虑清理的` (suggestions for caches of 1 GB or more).

JSON keys. Every key below is part of the output contract; `schema_version` changes if one is renamed or removed.

| Object | Key | Meaning |
| --- | --- | --- |
| top | `schema_version` | Layout version; `1` for this layout. |
| top | `generated_at` | Local ISO 8601 time of the check. |
| top | `elapsed_seconds` | Wall time of the whole run. |
| top | `budgets` | `max_entries_per_row` and `max_seconds` used for the run. |
| top | `drives` | One row per drive. |
| top | `storage` | The Stardust data home first, then the known caches. |
| top | `suggestions` | Cleanup suggestions. |
| `drives[]` | `drive` | Drive root, such as `C:\`, or `/`. |
| `drives[]` | `status` | `ok`, `low` (under 10 GB free, or under 10% free), or `error`. |
| `drives[]` | `free_bytes`, `total_bytes`, `free_percent` | Bytes and percent; null on error. |
| `drives[]` | `error` | Error text when the drive could not be read, else null. |
| `storage[]` | `kind` | `data` (Stardust home) or `cache`. |
| `storage[]` | `name`, `path`, `display_path` | Label, absolute path, and the path shown with `~`. |
| `storage[]` | `path_source`, `env_var` | `env` or `default`, and the variable that set the path (or null). |
| `storage[]` | `present` | False when the folder does not exist. |
| `storage[]` | `bytes`, `entries` | File bytes counted and directory entries examined. |
| `storage[]` | `complete` | False when a budget, an unreadable folder, or a link ended the count. |
| `storage[]` | `stop_reason` | null, `file_budget`, `time_budget`, `unreadable`, or `link_not_followed`. |
| `storage[]` | `clean_command` | The tool's own cleanup command for the cache, or null. |
| `suggestions[]` | `name`, `bytes`, `complete` | As in `storage[]`; only caches of 1 GB or more appear. |
| `suggestions[]` | `command`, `command_kind` | The command to print, or null; `native`, `delete_path`, or `none`. |

## Procedure

1. Run the script with no flags and show the owner the three sections in plain Chinese: free space on each drive, the sizes of the data home and the caches, and any suggestions.
2. Mark every size that starts with 至少 as a lower bound. Rerun with larger `--max-seconds` or `--max-entries` before quoting a total.
3. For each suggestion, show the owner the command and let them run it. Do not run a cleanup or delete command yourself, and do not treat the report as permission to delete.
4. For a morning brief, run with `--json` or the default output and take the drive rows and the data home row. Quote sizes in GB to one decimal place.
5. When a drive or cache is missing or unreadable, say so in the report. Do not guess its size.

## Pitfalls

- A row marked 至少 is a lower bound. It stopped at the per-row entry budget (`file_budget`) or at the whole-run time budget (`time_budget`). The default time budget is 8 seconds for all rows together, so large caches on a slow disk can be cut short. On the maintainer's Windows machine the walk ran at roughly 7,500 to 15,000 entries per second.
- The drive check is not covered by the time budget. A disconnected mapped network drive can take a long time to answer.
- Symbolic links and junctions are never followed or counted. A cache folder that is a link reports `link_not_followed` and no size.
- The Stardust data home is measured but never suggested for cleanup. A cache path that is a drive root, the home folder itself or an ancestor of it, or inside or above the data home gets `command_kind` `none` and no printed command.
- Cache locations come from environment variables first (for example `npm_config_cache`, `UV_CACHE_DIR`, `PIP_CACHE_DIR`, `ELECTRON_CACHE`, `PUPPETEER_CACHE_DIR`, `PLAYWRIGHT_BROWSERS_PATH`), then the platform default. `path_source` and `env_var` show which one applied.
- Sizes use 1024-based units (1 GB = 1024 MB), matching Windows Explorer.
- The script stats entries only. It never opens a file, reads configuration, `.env`, tokens, or credentials, and never prints file names.

## Verification

- `house_check.py --json` parses as JSON, `schema_version` is `1`, and the keys match the table above.
- The Markdown output has the three headings `## 磁盘`, `## 缓存与数据`, and `## 可以考虑清理的`, in that order.
- A run that reports 至少 or 未完成统计 is marked as incomplete in the answer to the owner.
