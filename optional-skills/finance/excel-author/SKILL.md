---
name: excel-author
description: Build auditable financial workbooks headless via openpyxl.
version: 1.0.0
author: Anthropic (adapted by Nous Research)
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [excel, openpyxl, finance, spreadsheet, modeling]
    related_skills: [xlsx, pptx-author, dcf-model, comps-analysis, lbo-model, 3-statement-model]
---

# excel-author

This skill is now a pointer. Use the xlsx skill for all workbook work, including financial models.

The banker-grade model conventions moved to the `xlsx` skill at `references/excel-author-conventions.md` (repo path `skills/productivity/xlsx/references/excel-author-conventions.md`). They cover blue/black/green inputs, formulas over hardcodes, named ranges, the Checks tab, cell-comment sourcing, the skeleton model, sensitivity-table rules, and the checkpoint pattern.

Recalculation moved too. Use the xlsx skill's `scripts/xlsx_recalc.py`. Its `--timeout` flag replaces the old positional timeout, and its JSON reports `"recalculated": true` where the old script reported `"status": "success"`.

## Attribution

Conventions adapted from Anthropic's Claude for Financial Services plugin suite, Apache-2.0 licensed. Original: https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/xlsx-author
