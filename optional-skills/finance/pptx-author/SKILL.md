---
name: pptx-author
description: Build PowerPoint decks headless with python-pptx.
version: 1.0.0
author: Anthropic (adapted by Nous Research)
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [powerpoint, pptx, python-pptx, presentation, finance]
    related_skills: [excel-author, powerpoint]
---

# pptx-author

This skill is now a pointer. Use the powerpoint skill for all deck work, including model-backed pitch decks, IC memos, and earnings notes.

The pitch-deck conventions moved to the `powerpoint` skill at `references/pptx-author-conventions.md` (repo path `skills/productivity/powerpoint/references/pptx-author-conventions.md`). They cover one idea per slide, numbers traced to the source workbook, firm templates, PNG charts, and the pitch-deck slide checklist.

Before binding deck numbers to a workbook, recalculate the workbook with the xlsx skill's `scripts/xlsx_recalc.py`.

## Attribution

Conventions adapted from Anthropic's Claude for Financial Services plugin suite, Apache-2.0 licensed. Original: https://github.com/anthropics/financial-services/tree/main/plugins/agent-plugins/pitch-agent/skills/pptx-author
