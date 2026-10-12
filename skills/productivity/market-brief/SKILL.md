---
name: market-brief
description: "Daily world stock brief with cited figures, saved as PDF."
version: 0.2.0
author: 9529360-cpu (Stardust)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Markets, Stocks, Finance, Briefing, PDF, Reports]
    category: productivity
    related_skills: [stocks, pdf, competitor-news-monitor]
---

# Market Brief Skill

Produces a daily brief of global stock indices (United States, Europe, Asia), notable movers and macro news. Index levels come from the stocks skill (Yahoo Finance), movers and news come from web pages you open, and the PDF is rendered by the pdf skill. Every figure carries a source, and a figure that cannot be found is written as 未取到. The brief states facts only: no buy or sell advice and no trades.

## When to Use

- The user asks for the market picture: "今天全球股市怎么样", "股市日报", "global market brief".
- The scheduled "全球股市日报" automation fires (the `global-market-brief` blueprint, default 18:00 local).

Don't use for a single quote (run the stocks skill's `quote` command directly), for one company's research (use `web_search` or `web_extract`), or for personal investment advice.

## Prerequisites

- Web access through `web_search` and `web_extract` for movers and macro news. A mover or news figure must come from a page you opened, not from a search snippet alone.
- The stocks skill (`finance/stocks`, Python stdlib, no API key) in the same skills folder. It is optional. If it is missing, every index row reads 未取到, and the reply tells the user to install it with `stardust skills install official/finance/stocks`.
- The built-in pdf skill (`productivity/pdf`) and its `scripts/pdf_markdown.py`. The pdf skill documents its own Python packages and CJK font. This skill installs nothing; if the script reports a missing package or font, pass that message on. If the script cannot run at all, keep the Markdown brief and say the PDF was not made.
- Run the reports-folder command and the pdf script with the Stardust environment's `python`, the one that can import `hermes_constants`.

## How to Run

`${STARDUST_SKILL_DIR}` is this skill's folder, and the loader fills it in. The stocks and pdf skills sit beside this one in the same skills folder.

```
python "${STARDUST_SKILL_DIR}/../../finance/stocks/scripts/stocks_client.py" quote "^GSPC" "^IXIC" "^DJI"
python -c "from hermes_constants import get_hermes_home, mkdir_under_hermes_home; print(mkdir_under_hermes_home(get_hermes_home() / 'workspace' / 'reports'))"
python "${STARDUST_SKILL_DIR}/../pdf/scripts/pdf_markdown.py" --input "<reports>/YYYY-MM-DD-market-brief.md" --output "<reports>/YYYY-MM-DD-market-brief.pdf" --title "全球股市日报 · YYYY-MM-DD"
```

The stocks command prints JSON on stdout. The pdf script prints one JSON object on stdout with `output`, `pages`, `cjk_font` and `warnings`.

Both relative paths resolve in an installed skills folder, where the stocks and pdf skills sit beside this one. If a path is missing (the sibling is not installed in this profile, or was archived), load that skill with `skill_view` and use the `skill_dir` it returns in place of the relative path. If it is still unavailable, follow the rules in Prerequisites.

## Quick Reference

| Step | Tool | Result |
|---|---|---|
| Index levels | `terminal`: stocks `quote` | `price` and `change_pct` per symbol |
| Movers, macro news | `web_search`, `web_extract` | three or more publishers, each with a link |
| Reports folder | `terminal`: one-line `python -c` | `<home>/workspace/reports`, created if missing |
| Brief text | `write_file` | `<reports>/YYYY-MM-DD-market-brief.md` |
| PDF | `terminal`: `pdf_markdown.py` | JSON with `output`, `pages`, `cjk_font`, `warnings` |
| Reply | chat | short summary, 未取到 count, PDF path |

## Procedure

### 1. Fix the date and session status

Use today's date in the local timezone (YYYY-MM-DD) and note the current local time. Check which sessions have closed at that time: Asian markets usually close during the day, European markets may still be trading, and US markets may not have opened yet. Every figure gets one status: 收盘 (closed), 盘中 (intraday) or 前一交易日收盘 (previous session close). On a weekend or market holiday, write 休市 and show the last session's close with its date. Done when the as-of time and the status labels are fixed.

### 2. Index levels from the stocks skill

Run `quote` once per region, with all of that region's symbols in one call:
- United States: `"^GSPC"` (标普500, S&P 500), `"^IXIC"` (纳斯达克综合指数, Nasdaq Composite), `"^DJI"` (道琼斯工业平均指数, Dow Jones Industrial Average).
- Europe: `"^STOXX"` (斯托克欧洲600, STOXX Europe 600), `"^FTSE"` (富时100, FTSE 100), `"^GDAXI"` (德国DAX, DAX), `"^FCHI"` (法国CAC40, CAC 40).
- Asia: `"^N225"` (日经225, Nikkei 225), `"^HSI"` (恒生指数, Hang Seng Index), `"000001.SS"` (上证综指, Shanghai Composite), `"^KS11"` (韩国综合指数, KOSPI).

Keep the symbols in double quotes: cmd.exe reads `^` as an escape. With several symbols the output is a JSON array, and with one symbol it is a single object. Match each object by its `symbol` field:
- `price` is the level (最新). `change_pct` is the day change (涨跌幅). Both are strings, for example `"1234.56"` and `"-0.42%"`. Keep them as printed.
- The output has no plus sign. Write one in front of a positive change.
- A missing `price` key, or a null one, means Yahoo returned nothing for that symbol. Retry that symbol once after a short pause. If it is still empty, both 最新 and 涨跌幅 read 未取到.
- If the command fails or the stocks skill is missing, every index row reads 未取到. Never take an index level from a news page.
- The output has no previous close, so the table shows the level and the percent change only.

Done when every index row has a level and a change from this run's output, or reads 未取到.

### 3. Find notable movers

Pick up to three notable moves per region from web search results, and open the pages with `web_extract`. Choose stocks or sectors with the largest moves, with the reason a source gives. Each mover needs a signed percent change, its trading date, a label of 收盘 or 盘中, and a cited reason. Done when each mover names its source, or its reason reads 未取到.

### 4. Collect macro news

Find three to five items from the same day or the day before: central bank decisions, inflation or jobs data, trade or tariff news, oil, currencies, bond yields. Open each item's page with `web_extract`. Each item gets one line, a date and a link. Movers and news together cite at least three different publishers. Done when every item has a date and a source.

### 5. Write the Markdown brief

Use this outline, in this order, in Simplified Chinese. Keep index and company names in their usual form.

```
# 全球股市日报 · YYYY-MM-DD

数据截至 HH:MM（时区）。状态：收盘 / 盘中 / 前一交易日收盘。

## 主要指数

| 地区 | 指数 | 最新 | 涨跌幅 | 状态 | 来源 |
|---|---|---|---|---|---|
| 美国 | 标普500 (S&P 500) | <price> | <change_pct> | <状态> | [Yahoo Finance (stocks skill)](https://finance.yahoo.com/quote/%5EGSPC) |

## 主要异动

- **名称 (市场)**: +1.2%，原因。来源：[来源名](https://...)

## 宏观要闻

- YYYY-MM-DD · 要点，一句话。来源：[来源名](https://...)

## 资料来源

1. [Yahoo Finance](https://finance.yahoo.com/) · 指数数据，经 stocks skill 取得，访问于 HH:MM
2. [来源名](https://...) · 访问于 HH:MM
```

Rules for figures:
- Never invent a number. A figure the stocks output does not carry, or a web figure that is not on a page you opened, is 未取到. Do not estimate, round from memory or reuse an older figure.
- Keep the source's units. Write percent changes with a sign, plus or minus.
- State facts only. Do not write buy, sell or hold advice.
- Each index link goes to that symbol's Yahoo quote page: `https://finance.yahoo.com/quote/` followed by the symbol, with `^` written as `%5E`.

Done when the file has all five sections in order and every missing figure reads 未取到.

### 6. Render the PDF and reply

1. Run the one-line `python -c` command from How to Run. It prints the reports folder under the Stardust home (`workspace/reports`) and creates it if missing. Use that path as `<reports>`.
2. Save the brief with `write_file` as `<reports>/YYYY-MM-DD-market-brief.md`.
3. Run the pdf script with `--input` set to that Markdown file, `--output` set to `<reports>/YYYY-MM-DD-market-brief.pdf`, and `--title` set to the H1 text.
4. Read the JSON on stdout. The script must exit 0. `output` is the PDF path and `pages` is the page count. If `cjk_font` is null, Chinese text may show as boxes; say so. Pass on each entry in `warnings`.

Reply to the user in plain Chinese with three to five lines on the main moves, the number of 未取到 items, the time the data was taken, and the PDF path. Done when the pdf script has exited 0 and the reply names the PDF path.

## Pitfalls

- Intraday numbers presented as closes. Label every intraday figure 盘中.
- Yahoo's endpoint is unofficial and may rate-limit. A missing price reads 未取到; never fill it from another site.
- The stocks output has no previous close and no plus sign. Do not add either from memory.
- Search snippets can be stale or wrong. Check each mover and news figure on the fetched page.
- Three news sources must be different publishers, not three copies of one wire story.
- Weekend or holiday runs: write 休市 instead of inventing a session.
- Without a CJK font the PDF shows boxes for Chinese. The Markdown file remains the complete record.

## Verification

- The Markdown has the five sections in the fixed order, and every figure has a source or reads 未取到.
- Every index level and change comes from the stocks output of this run.
- At least three different publishers are cited for movers and macro news.
- The pdf script exited 0, and its JSON `output` is the PDF in the reports folder.
