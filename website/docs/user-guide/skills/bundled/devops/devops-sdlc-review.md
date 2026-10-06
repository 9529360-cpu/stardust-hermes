---
title: "Sdlc Review — Review implementation evidence and report defects"
sidebar_label: "Sdlc Review"
description: "Review implementation evidence and report defects"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Sdlc Review

Review implementation evidence and report defects.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/devops\sdlc-review` |
| Version | `1.2.0` |
| Author | Jakub Wolniewicz (@frizikk) + Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `review`, `quality`, `verification` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# SDLC Review Skill

Independently verify an implementation against the user's requirements and the supplied evidence. Report whether it meets the acceptance criteria, which defects remain, and what requires a user decision.

## When to Use

- The user requests review of a branch, pull request, or completed implementation.
- A parent agent delegates an independent verification task with a defined scope.

## Prerequisites

- The original objective, constraints, acceptance criteria, and implementation location.
- Access to relevant files through `read_file`, `search_files`, and `terminal`.
- The implementation summary and available test results. Missing evidence must be identified explicitly.

## How to Run

Read the original request and handoff before inspecting the changed files. Trace the production paths affected by the change and verify the candidate actually satisfies the request.

## Quick Reference

| Outcome | Evidence | Report |
| --- | --- | --- |
| Ready | Acceptance criteria and required checks pass | Summarize the verified candidate and limits. |
| Changes required | A reproducible defect or unmet requirement remains | Give the location, impact, and expected behavior. |
| Needs input | A user decision or external prerequisite blocks verification | State the missing fact and the work already verified. |

## Procedure

1. Recover the task contract from the current request and handoff. Do not invent requirements from old conversation history.
2. Inspect the full diff, including deletions, registration, callers, generated contracts, and documentation.
3. Trace state ownership and affected runtime paths. Check authorization, data preservation, caching, and recovery where the change touches those boundaries.
4. Run the repository's required checks against the exact candidate. Record the commands, candidate identity, results, and any unrelated baseline failures.
5. Verify the user-visible flow at the strongest practical boundary. A mock or static check alone does not prove a desktop flow or external effect.
6. Recheck previously reported defects against the current code. Do not repeat resolved findings or approve based only on the implementer's summary.
7. Return a concise review with actionable defects and supporting evidence. Approval of the implementation does not itself authorize publishing, deployment, or merging.

## Pitfalls

- Claiming tests passed when they were not run.
- Treating missing evidence as proof of correctness.
- Expanding the review into unrelated implementation work.
- Overwriting another worker's changes or user data while verifying.

## Verification

- Every acceptance criterion has evidence or an explicit unresolved limitation.
- Every reported defect identifies the affected behavior and a concrete location.
- Results refer to the exact candidate reviewed.
- The final report distinguishes implementation defects from missing user input and unrelated baseline failures.
