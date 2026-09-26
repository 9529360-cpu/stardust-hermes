---
name: cli-anything
description: Install and run CLI-Hub's agent-native CLIs for software.
version: 0.1.0
author: CLI-Anything (HKUDS), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cli-hub, cli-anything, agent-tooling, software-automation, harness]
    related_skills: [openhands]
---

# CLI-Anything / CLI-Hub

Discover, install, and drive [CLI-Hub](https://github.com/HKUDS/CLI-Anything)'s
community registry of agent-native CLI wrappers ("harnesses") for real desktop
and server software — image editors, 3D/CAD tools, office suites, note-taking
apps, media tools, and more — via the `terminal` tool. Each harness is a
self-contained pip package with a scriptable, JSON-capable CLI in front of the
real application, so this is for *using* software an agent otherwise can't
reach (no GUI automation, no screen scraping) — not for writing code, which
belongs to the project's own tools.

## When to Use

- The user wants a task done in a specific desktop/creative/office application
  (e.g. "resize this image in GIMP", "convert this ebook with Calibre",
  "add a note to my Obsidian vault") and no dedicated skill/tool for that
  application already exists.
- A task spans several tools toward one outcome (e.g. produce a video: transcribe
  audio, generate a voiceover, edit the timeline) — reach for a **matrix**
  instead of installing tools one at a time (see below).
- Before assuming "no way to control this app" — check the registry first.

Do not use this for tasks a native Hermes tool already covers (file edits,
web browsing, code execution) — CLI-Hub is for reaching *other* software.

## Prerequisites

Install the CLI-Hub package manager once per environment:

```
terminal(command="pip install cli-anything-hub")
```

Verify: `terminal(command="cli-hub --version")`.

Some harnesses wrap a real backend application (GIMP, Blender, LibreOffice,
etc.). If a chosen harness needs one, install that upstream application too —
`cli-hub info <name>` states the requirement.

## How to Run

Always invoke through the `terminal` tool. Prefer `--json` wherever a
subcommand supports it so output is parseable rather than prose.

### Find and install a single tool

```
terminal(command="cli-hub search image")
terminal(command="cli-hub info gimp")
terminal(command="cli-hub install gimp")
```

Installing `<name>` installs the `cli-anything-<name>` pip package and exposes
a `cli-anything-<name>` console script.

### Use it

```
terminal(command="cli-anything-gimp --json open --file /path/to/image.png")
```

Each harness defaults to a REPL when run with no subcommand; pass a
subcommand for one-shot, non-interactive use (the shape automation needs).

### Multi-tool workflows: use a matrix, not manual installs

A matrix packages a whole workflow (capabilities × providers) — reach for one
when the task needs more than one tool:

```
terminal(command="cli-hub matrix list")
terminal(command="cli-hub can \"transcribe audio\"")
terminal(command="cli-hub matrix preflight video-creation --json")
terminal(command="cli-hub matrix install video-creation --capability text.transcribe")
```

Scope every matrix install to what the task actually needs — `--capability
<id>`, `--recipe <id>`, or `--only a,b` — and preview first with `--dry-run`.
Exit codes: `0` ok, `3` partial/gaps, `1` failure, `2` usage error.

## Quick Reference

| Command | Effect |
|---|---|
| `cli-hub list` / `list -c <category>` | Browse the registry |
| `cli-hub search <query>` | Search by keyword |
| `cli-hub info <name>` | Inspect one CLI's requirements/commands |
| `cli-hub install <name>` / `update <name>` / `uninstall <name>` | Manage one harness |
| `cli-hub launch <name> [args...]` | Run an installed harness |
| `cli-hub matrix list` / `matrix search <q>` | Browse/search workflow matrices |
| `cli-hub matrix preflight <matrix> [--capability <id>] --json` | Check what's usable before installing |
| `cli-hub matrix install <matrix> --capability <id>` | Install only what one capability needs |
| `cli-hub matrix doctor <matrix>` | Audit an existing matrix install |

## Pitfalls

- **`cli-hub` is a thin wrapper around `pip`.** Each harness is its own
  independent pip package (`cli-anything-<name>`); there is no shared runtime
  to break, but uninstalling is also just a normal pip uninstall.
- **Don't bulk-install a whole matrix for a one-capability task.** Always pass
  `--capability`/`--only` and check `preflight` first — matrices can bundle a
  dozen+ CLIs.
- **A harness wrapping desktop software needs that software present.** CLI-Hub
  does not install GIMP/Blender/LibreOffice itself; `cli-hub info <name>`
  states the upstream dependency before you commit to installing.
- **This is a community registry, not a Hermes-reviewed one.** Prefer it for
  reaching real software with no existing coverage; treat any harness's
  destructive operations (file overwrite, library conversion, deletion) with
  the same caution as any other unreviewed third-party CLI.
- **Building a brand-new harness is a separate, heavier workflow** (the
  project's 7-phase generator) and is out of scope for this skill, which
  covers discovering and using what already exists in the registry.

## Verification

```
terminal(command="pip install cli-anything-hub")
terminal(command="cli-hub list")
```

If `cli-hub list` returns a non-empty registry (dozens of entries across
categories like creative, productivity, AI, development), the install is
working.

## Related

- [CLI-Anything / CLI-Hub](https://github.com/HKUDS/CLI-Anything) (Apache-2.0) — upstream project, registry, and the harness-generation methodology this skill does not cover.
- Web hub: https://clianything.cc
