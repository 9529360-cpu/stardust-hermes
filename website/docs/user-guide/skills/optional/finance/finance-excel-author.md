---
title: "Excel Author — Build auditable financial workbooks headless via openpyxl"
sidebar_label: "Excel Author"
description: "Build auditable financial workbooks headless via openpyxl"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Excel Author

Build auditable financial workbooks headless via openpyxl.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/finance/excel-author` |
| Path | `optional-skills/finance\excel-author` |
| Version | `1.0.0` |
| Author | Anthropic (adapted by Nous Research) |
| License | Apache-2.0 |
| Platforms | linux, macos, windows |
| Tags | `excel`, `openpyxl`, `finance`, `spreadsheet`, `modeling` |
| Related skills | [`xlsx`](/docs/user-guide/skills/bundled/productivity/productivity-xlsx), [`pptx-author`](/docs/user-guide/skills/optional/finance/finance-pptx-author), [`dcf-model`](/docs/user-guide/skills/optional/finance/finance-dcf-model), [`comps-analysis`](/docs/user-guide/skills/optional/finance/finance-comps-analysis), [`lbo-model`](/docs/user-guide/skills/optional/finance/finance-lbo-model), [`3-statement-model`](/docs/user-guide/skills/optional/finance/finance-3-statement-model) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# excel-author

This skill is now a pointer. Use the xlsx skill for all workbook work, including financial models.

The banker-grade model conventions moved to the `xlsx` skill at `references/excel-author-conventions.md` (repo path `skills/productivity/xlsx/references/excel-author-conventions.md`). They cover blue/black/green inputs, formulas over hardcodes, named ranges, the Checks tab, cell-comment sourcing, the skeleton model, sensitivity-table rules, and the checkpoint pattern.

Recalculation moved too. Use the xlsx skill's `scripts/xlsx_recalc.py`. Its `--timeout` flag replaces the old positional timeout, and its JSON reports `"recalculated": true` where the old script reported `"status": "success"`.

## Attribution

Conventions adapted from Anthropic's Claude for Financial Services plugin suite, Apache-2.0 licensed. Original: https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/xlsx-author
