---
name: market-brief
description: "Daily world stock brief with cited figures, saved as PDF."
version: 0.1.0
author: 9529360-cpu (Stardust)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Markets, Stocks, Finance, Briefing, PDF, Reports]
    category: productivity
    related_skills: [pdf, competitor-news-monitor]
---

# Market Brief Skill

Produces a daily brief of global stock indices (United States, Europe, Asia), notable movers and macro news. Every figure carries a source link, and a figure that cannot be found is written as 未取到. The brief is saved as Markdown and rendered to PDF in the assistant's reports folder. It states facts only: no buy or sell advice and no trades.

## When to Use

- The user asks for the market picture: "今天全球股市怎么样", "股市日报", "global market brief".
- The scheduled "全球股市日报" automation fires (the `global-market-brief` blueprint, default 18:00 local).

Don't use for a single quote or one company's research (use `web_search` or `web_extract` directly), or for personal investment advice.

## Prerequisites

- Web access through `web_search` and `web_extract`. A figure must come from a page you opened, not from a search snippet alone.
- reportlab from the `pdf` extra in the Stardust Python environment. The extra is part of `all`, so re-run the sync you normally use (for example `uv sync --locked --extra all`). The script exits with an install hint when reportlab is missing.
- A CJK font for Chinese text. The script embeds Microsoft YaHei (`msyh.ttc`) on Windows, or a TrueType CJK font on Linux or macOS (for example `fonts-wqy-zenhei`). Without one it still writes the PDF, but Chinese shows as boxes and the script prints a warning. Pass that warning on to the user.

## How to Run

```
uv run python SKILL_DIR/scripts/make_brief_pdf.py --reports-dir        # prints the reports folder
uv run python SKILL_DIR/scripts/make_brief_pdf.py BRIEF.md [OUT.pdf]   # renders the PDF
```

`SKILL_DIR` is the directory containing this SKILL.md file. When the render finishes, the script's last printed line is the PDF path.

## Quick Reference

| Step | Tool | Result |
|---|---|---|
| Reports folder | `terminal` | `make_brief_pdf.py --reports-dir` |
| Index levels, movers, news | `web_search`, `web_extract` | three or more publishers, each with a link |
| Brief text | `write_file` | `<reports>/YYYY-MM-DD-market-brief.md` |
| PDF | `terminal` | `make_brief_pdf.py <reports>/YYYY-MM-DD-market-brief.md` |
| Reply | chat | short summary, 未取到 count, PDF path |

## Procedure

### 1. Fix the date and session status

Use today's date in the local timezone (YYYY-MM-DD) and note the current local time. Check which sessions have closed at that time: Asian markets usually close during the day, European markets may still be trading, and US markets may not have opened yet. Every figure gets one status: 收盘 (closed), 盘中 (intraday) or 前一交易日收盘 (previous session close). On a weekend or market holiday, write 休市 and show the last session's close with its date. Done when the as-of time and the status labels are fixed.

### 2. Gather index levels from at least three publishers

Run `web_search` for each region:
- United States: S&P 500, Nasdaq Composite, Dow Jones Industrial Average.
- Europe: STOXX Europe 600, FTSE 100, DAX, CAC 40.
- Asia: Nikkei 225, Hang Seng, Shanghai Composite, KOSPI.

Open the two or three best results per region with `web_extract` and read the level and percent change from the page. The brief must cite at least three different publishers across all rows. Done when every index row has a level, a change and a source, or is marked 未取到.

### 3. Find notable movers

Pick up to three notable moves per region: stocks or sectors with the largest moves, with the reason a source gives. Each mover needs a signed percent change and a cited reason. Done when each mover names its source, or its reason reads 未取到.

### 4. Collect macro news

Find three to five items from the same day or the day before: central bank decisions, inflation or jobs data, trade or tariff news, oil, currencies, bond yields. Each item gets one line, a date and a link. Done when every item has a date and a source.

### 5. Write the Markdown brief

Use this outline, in this order, in Simplified Chinese. Keep index and company names in their usual form.

```
# 全球股市日报 · YYYY-MM-DD

数据截至 HH:MM（时区）。状态：收盘 / 盘中 / 前一交易日收盘。

## 主要指数

| 地区 | 指数 | 最新 | 涨跌幅 | 状态 | 来源 |
|---|---|---|---|---|---|
| 美国 | 标普500 (S&P 500) | 未取到 | 未取到 | 前一交易日收盘 | [来源名](https://...) |

## 主要异动

- **名称 (市场)**: +1.2%，原因。来源：[来源名](https://...)

## 宏观要闻

- YYYY-MM-DD · 要点，一句话。来源：[来源名](https://...)

## 资料来源

1. [来源名](https://...) · 访问于 HH:MM
```

Rules for figures:
- Never invent a number. A figure that is not on a page you opened is 未取到. Do not estimate, round from memory or reuse an older figure.
- Keep the source's units and currency. Write percent changes with a sign.
- State facts only. Do not write buy, sell or hold advice.

Done when the file has all five sections in order and every missing figure reads 未取到.

### 6. Render the PDF and reply

Use `terminal` to get the reports folder with `make_brief_pdf.py --reports-dir`. Save the brief there with `write_file` as `YYYY-MM-DD-market-brief.md`, then render it. The PDF lands beside it as `YYYY-MM-DD-market-brief.pdf`. Pass a second argument only to choose another path. Confirm that the script exited without error and that its last line is the PDF path.

Reply to the user in plain Chinese with three to five lines on the main moves, the number of 未取到 items, the time the data was taken, and the PDF path. If the script printed a font warning, say so. Done when the PDF exists and the reply names its path.

## Pitfalls

- Intraday numbers presented as closes. Label every intraday figure 盘中.
- Search snippets can be stale or wrong. Check each figure on the fetched page.
- Three sources must be different publishers, not three copies of one wire story.
- Weekend or holiday runs: write 休市 instead of inventing a session.
- Without a CJK font the PDF shows boxes for Chinese. The Markdown file remains the complete record.
- reportlab must be installed (see Prerequisites). The script does not install it itself.

## Verification

- The Markdown has the five sections in the fixed order, and every figure has a source or reads 未取到.
- At least three different publishers are cited across the brief.
- The script exited with status 0, its last line is the PDF path, and the PDF is in the reports folder.
