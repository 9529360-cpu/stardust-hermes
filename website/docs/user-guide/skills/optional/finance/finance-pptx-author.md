---
title: "Pptx Author — Build PowerPoint decks headless with python-pptx"
sidebar_label: "Pptx Author"
description: "Build PowerPoint decks headless with python-pptx"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Pptx Author

Build PowerPoint decks headless with python-pptx.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/finance/pptx-author` |
| Path | `optional-skills/finance\pptx-author` |
| Version | `1.0.0` |
| Author | Anthropic (adapted by Nous Research) |
| License | Apache-2.0 |
| Platforms | linux, macos, windows |
| Tags | `powerpoint`, `pptx`, `python-pptx`, `presentation`, `finance` |
| Related skills | [`excel-author`](/docs/user-guide/skills/optional/finance/finance-excel-author), [`powerpoint`](/docs/user-guide/skills/bundled/productivity/productivity-powerpoint) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# pptx-author

This skill is now a pointer. Use the powerpoint skill for all deck work, including model-backed pitch decks, IC memos, and earnings notes.

The pitch-deck conventions moved to the `powerpoint` skill at `references/pptx-author-conventions.md` (repo path `skills/productivity/powerpoint/references/pptx-author-conventions.md`). They cover one idea per slide, numbers traced to the source workbook, firm templates, PNG charts, and the pitch-deck slide checklist.

Before binding deck numbers to a workbook, recalculate the workbook with the xlsx skill's `scripts/xlsx_recalc.py`.

## Attribution

Conventions adapted from Anthropic's Claude for Financial Services plugin suite, Apache-2.0 licensed. Original: https://github.com/anthropics/financial-services/tree/main/plugins/agent-plugins/pitch-agent/skills/pptx-author
