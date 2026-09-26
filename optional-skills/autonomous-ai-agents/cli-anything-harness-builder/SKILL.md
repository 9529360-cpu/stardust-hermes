---
name: cli-anything-harness-builder
description: Build a new agent-usable CLI harness for GUI software.
version: 0.1.0
author: CLI-Anything (HKUDS), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cli-hub, cli-anything, agent-tooling, harness-generation, software-automation]
    related_skills: [cli-anything]
---

# CLI-Anything Harness Builder

Turn a GUI application (or any software with no agent-friendly interface) into
a scriptable, JSON-capable CLI you can then drive with `terminal` — the same
methodology behind [CLI-Anything](https://github.com/HKUDS/CLI-Anything)'s
registry, condensed for Hermes tools (`terminal`, `read_file`/`write_file`,
`patch`, `delegate_task`) instead of the upstream doc's Claude-Code/Codex
framing. This is a bigger job than using an existing harness — expect several
turns of real engineering, not a one-shot script.

## When to Use

- The user wants Stardust to operate a specific piece of software and no
  harness for it exists yet. **Check first**: use the `cli-anything` skill's
  `cli-hub search <name>` — if a harness already exists, install and use it
  instead of rebuilding it.
- The user explicitly asks to "build a CLI for X" / "make X agent-usable."

Do not reach for this to wrap something that already has a decent CLI or a
Hermes-native tool — this is for GUI-only or no-interface software.

## The One Non-Negotiable Rule

**Call the real software. Never reimplement it.** The #1 failure mode is
building a look-alike (a Pillow-based image compositor standing in for GIMP, a
`bpy`-script generator that never actually invokes Blender) — it can't handle
real workloads and silently diverges from the real thing. The software is a
**hard runtime dependency** you shell out to via `terminal`, documented in the
harness's own README with install instructions per OS — never an optional
fallback, never a "gracefully degraded" pure-Python reimplementation.

The shape is always: **build valid project/intermediate data → hand it to the
real software via its CLI/scripting interface → verify the real output.**

| Software | Backend invocation | Native format |
|---|---|---|
| LibreOffice | `libreoffice --headless --convert-to <fmt>` | ODF (ZIP+XML) |
| Blender | `blender --background --python script.py` | `.blend` / bpy script |
| GIMP | `gimp -i -b '(script-fu-console-eval ...)'` | `.xcf` |
| Inkscape | `inkscape --actions="..." --export-filename=...` | SVG |
| Shotcut / Kdenlive | `melt project.mlt -consumer avformat:out.mp4` | MLT XML |
| Audacity | `sox` | `.aup3` |
| Browser (no CLI) | an MCP server, when one exists — see `references/architecture-and-pitfalls.md` | n/a |

## Workflow

### 1. Analyze

Find the backend engine (the library/CLI the GUI itself calls), the native
project-file format, and any command/undo system — those commands ARE your
future CLI operations. Use `terminal`/`read_file` to inspect the target
software's source (if available) or its CLI `--help`/scripting docs.

### 2. Design

- Interaction model: build **both** a stateful REPL (default, no args) and
  one-shot subcommands (scripting) — not just one.
- Command groups by domain: project (new/open/save), core operations
  (the app's actual purpose), import/export, config, session (undo/redo/status).
- Every command supports `--json` for machine output.
- Plan the state model: what persists between commands, and where.

### 3. Implement

Use `write_file`/`patch` to build, in this exact layout (PEP 420 namespace
package — `cli_anything/` itself has **no** `__init__.py`, so unrelated
harnesses can coexist in one Python environment):

```
<software>/agent-harness/
├── <SOFTWARE>.md              # your analysis notes (phase 1)
├── setup.py
└── cli_anything/
    └── <software>/
        ├── __init__.py
        ├── __main__.py
        ├── README.md           # install + run instructions — required
        ├── <software>_cli.py   # Click entry point; REPL when no subcommand
        ├── core/               # one module per domain, e.g. project.py, export.py, session.py
        ├── utils/
        │   └── <software>_backend.py   # the ONLY module that shells out to the real software
        └── tests/
            ├── TEST.md         # test plan, then results — required
            ├── test_core.py
            └── test_full_e2e.py
```

Read `references/architecture-and-pitfalls.md` before writing `export.py` or
any rendering path — the "rendering gap" (effects silently dropped because the
renderer you picked ignores project-level filters) is the #2 failure mode.

### 4–5. Test (plan first, then implement)

Write `tests/TEST.md` as a plan **before** any test code: which modules,
which edge cases, which realistic multi-step workflows (e.g. "podcast
production," "YouTube-style cut/trim") will be simulated, and what output
properties get verified. Then implement against that plan.

Read `references/testing-and-verification.md` before writing `test_full_e2e.py`
— this is the layer that actually invokes the real software and is the one
projects most often skip or fake. **No graceful degradation**: if the real
software isn't installed, tests fail, they don't skip.

Append real `pytest -v` results to `TEST.md` once everything passes.

### 6. Give the harness its own skill

Generate a `SKILL.md` for the finished harness (frontmatter `name`/
`description`, then install prerequisites, command groups, `--json` usage,
realistic examples) so Stardust — or any other agent — can discover and drive
it later without re-reading your source. This is a skill for the *harness*,
separate from this builder skill.

### 7. Package it

Read `references/packaging.md` for the exact `setup.py` namespace-package
template and verification steps (`pip install -e .`, then run the test suite
against the **installed** command, not a module fallback).

## Verification Before Calling It Done

- `pip install -e .` succeeds and `cli-anything-<software> --help` runs from
  any directory (not just the source tree).
- `CLI_ANYTHING_FORCE_INSTALLED=1 pytest cli_anything/<software>/tests/ -v -s`
  passes, and its output confirms the installed binary was used, not a
  `python -m` fallback.
- At least one E2E test produced a real artifact (PDF/DOCX/rendered
  image/video) verified by more than "it ran without error" — magic bytes,
  ZIP structure, or pixel/audio inspection as appropriate. Print the artifact
  path so the user can open it themselves.

## Pitfalls

- **Building a toy instead of a harness.** If you catch yourself writing
  rendering logic in Python instead of shelling out to the real software,
  stop — see the Non-Negotiable Rule above.
- **Trusting exit code 0.** An export that "succeeds" but produces a
  corrupt or empty file is a common failure; always verify the artifact
  itself (see `references/testing-and-verification.md`).
- **Skipping TEST.md.** Writing test code before the plan is exactly how test
  suites end up covering the easy 20% and missing the realistic workflows.
- **Forgetting `--json`.** A harness without machine-readable output isn't
  actually agent-usable, just a CLI.
- **This is a multi-turn build, not a one-shot.** Budget for it; don't try to
  compress analysis, implementation, and real E2E testing into one response.

## Related

- [CLI-Anything](https://github.com/HKUDS/CLI-Anything) (Apache-2.0) — upstream project and the full, unabridged methodology (`cli-anything-plugin/HARNESS.md`) this skill condenses. Its `guides/` folder has deeper material this skill intentionally doesn't duplicate for software with specialized needs: `session-locking.md`, `preview-methodology.md`, `mcp-backend.md`, `filter-translation.md`, `timecode-precision.md` — fetch the relevant one from upstream if you're building a harness where it applies (e.g. video/audio effects or non-integer frame rates).
- `cli-anything` (sibling skill in this repo) — the consumer side: discovering and running harnesses that already exist, including the one this workflow just built.
