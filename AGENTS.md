# Stardust Development Guide

Instructions for AI coding assistants and developers working on **Stardust**.

Stardust is an independently maintained personal AI assistant built on the open-source Hermes Agent codebase. `9529360-cpu/stardust-hermes` is the source and release authority for this project. Hermes Agent is the technical foundation and historical code origin; it is not the product identity or an upstream branch that this repository must continuously follow.

This root file contains repository-wide rules. Each major area has its own `AGENTS.md`; always read the nearest area guide before editing that area. When an area guide and this file differ, the more specific area rule wins unless it would violate the Stardust ownership, privacy, or source-authority rules below.

**Never give up on the right solution. Do not substitute a cosmetic patch for the structural behavior the request requires.**

## Stardust Ownership Rules

These rules are authoritative for every part of the repository:

- **This repository is independently maintained.** Do not add periodic upstream merge/rebase jobs, automatic upstream synchronization, or update paths that replace Stardust with an upstream Hermes checkout.
- **New product-facing ownership belongs to Stardust.** New install URLs, update URLs, release metadata, recovery paths, documentation links, product copy, and repository links must point to `9529360-cpu/stardust-hermes` unless the maintainer explicitly chooses another Stardust-owned endpoint.
- **Hermes naming may remain at compatibility boundaries.** Python packages, the `hermes` CLI, `HERMES_HOME`, existing environment variables, IPC/protocol names, install/data paths, executable names, app IDs, and other inherited interfaces may be load-bearing. Do not rename them merely to make search results cleaner. A compatibility rename requires a migration plan, fallback/rollback behavior, existing-install coverage, and data-preservation proof.
- **Attribution stays.** Keep the MIT license, original copyright notices, useful Git history, and accurate references to Hermes Agent / Nous Research where they identify the code foundation or a real external service.
- **Upstream code may be studied, not followed by default.** A specific implementation may be manually adapted when it solves a concrete Stardust problem. That is an isolated engineering decision, not restoration of an upstream tracking relationship.
- **Personal/runtime data never belongs in Git.** Do not commit `.env`, API keys, tokens, credentials, private keys, conversations, memories, profile exports, logs, caches, route-health state, local databases, backups, or packaged apps containing personal state.

See `STARDUST.md` and `CONTRIBUTING.md` for the public maintenance policy.

## What Stardust Is

Stardust runs the inherited agent core across a CLI, messaging gateway, TUI, web/dashboard surfaces, and an Electron desktop application. The current product direction prioritizes a long-lived personal assistant: a calmer Chinese-first desktop, personal workflows, reliable model routing/failover, local/remote operation, and Stardust-owned install/update/recovery behavior.

The inherited architecture is valuable and should be evolved rather than casually rewritten. Two invariants shape almost every core design decision:

- **Per-conversation prompt caching is sacred.** A long-lived conversation reuses a cached prefix every turn. Do not mutate past context, swap toolsets, reload memories, or rebuild the system prompt mid-conversation unless the design explicitly handles cache invalidation. Context compression is the exceptional path. State-mutating slash commands should normally defer prompt-affecting changes to the next session unless the user explicitly asks for immediate application.
- **The core is a narrow waist; capability lives at the edges.** Every model tool is sent on every API call. Prefer extending existing behavior, CLI commands + skills, service-gated tools, plugins, or MCP before adding a permanent core tool.

## Engineering Priorities

In order of importance:

1. Correctness, data safety, privacy, security, and recoverability.
2. Preserve user intent and existing working behavior unless the request explicitly changes it.
3. Fix the whole bug class and all materially equivalent call paths, not one visible symptom.
4. Keep Stardust independent of upstream release infrastructure and credentials.
5. Maintain compatibility for inherited runtime interfaces unless a deliberate migration replaces them.
6. Prefer simple, observable, reversible architecture over hidden automation.
7. Add regression proof at the strongest practical boundary.
8. Improve personal-assistant UX without weakening core runtime correctness.

Before changing behavior, trace the real production path and identify where the state is actually owned. Do not patch a renderer symptom when Electron/backend state is authoritative, or patch a backend helper when the caller has already discarded the information you need.

## Capability Footprint

Choose the least permanent surface that correctly solves the problem:

1. Extend existing code.
2. CLI command + skill.
3. Service-gated or session-scoped toolset.
4. Plugin.
5. MCP server/catalog integration.
6. New core model tool only when it is fundamental and cannot be expressed through existing terminal/file/browser/tool surfaces.

Do not add speculative hooks, managers, callbacks, or environment variables with no concrete consumer.

### Surface capability belongs to the session

A capability that exists because of the client on the other end (desktop panes, Projects, reactions, in-app browser surfaces) must resolve from the session/connection, not from a process-wide environment variable. Desktop and backend may run on different machines. `HERMES_DESKTOP=1` can legitimately describe how a backend process was launched; it does not prove a GUI is watching the current session.

Keep session-specific tools in named toolsets selected by the session/platform resolver. Use `check_fn` for reachability or opt-in, not for “which client is connected?” decisions.

## Code Shape Rules

- Keep facade modules narrow. Put substantial new behavior in topical sibling modules rather than growing god files.
- A file approaching ~2,000 lines, a function approaching ~300 lines, or high cyclomatic complexity is a signal to split by responsibility before adding more behavior.
- Prefer tables/maps to long `if/elif` ladders keyed on ids, names, routes, or kinds.
- No dead compatibility shims for internal moves. External compatibility belongs in the explicit compatibility layer.
- Do not add “defense-in-depth” wrappers, silent `try/except: pass`, flags nobody sets, or fallback branches with no demonstrated failure mode.
- Never infer process identity from argv substring matching. Use the canonical process/holder matchers for that subsystem.
- Never hardcode `~/.hermes` for runtime state. Use `get_hermes_home()` for code paths and `display_hermes_home()` for user-facing text.
- `.env` is for secrets. Non-secret behavioral configuration belongs in `config.yaml` or the owning structured configuration surface.
- Moving a public/internal symbol means updating its real call sites and relevant docs in the same change. Do not leave stale paths because a compatibility alias happened to mask them.

### TypeScript / Desktop style

- Shared or distant UI state belongs in small nanostores near the feature that owns it; rendering components subscribe with `useStore`, non-rendering actions use `$atom.get()`.
- Route roots compose surfaces; they should not become controllers.
- Prefer narrow hooks/actions over monolithic “do everything” hooks.
- Extend React primitive prop types instead of duplicating them.
- Keep Electron, renderer, and backend ownership boundaries explicit. Native filesystem/process/update behavior belongs in Electron; rendering and interaction state belong in React; agent execution belongs in the backend/Gateway.
- `window.hermesDesktop`, `hermes://`, executable names, and similar inherited identifiers are compatibility surfaces until a tested Stardust migration replaces them. User-visible copy around them should still say Stardust where safe.
- Desktop product update affordances update the Stardust client. They must not re-enable the inherited upstream `hermes update` synchronization path.

## Source Authority and Task Workspace Discipline

The remote `origin/main` branch is the **single authoritative product line**. Local clones, worktrees, task branches, CI checkouts, recovery bundles, and developer machines are disposable working copies; none of them may become a second source of truth.

For every feature, bug fix, refactor, CI change, documentation change, or other repository modification:

1. **Start from fresh main.** Fetch `origin`, verify the local base is clean, and fast-forward to the current `origin/main`. Do not start new work from a stale task branch, an old worktree, or a developer-machine snapshot.
2. **One task, one branch.** Create one descriptively named task branch from the current `origin/main`. Keep one authoritative remote implementation lineage for that task. Do not create replacement branches or PR stacks merely to get a cleaner history or a fresh CI run.
3. **One task, one working tree per machine.** A developer machine may create a temporary clone or worktree for the active task, but that workspace must track the task branch. Never keep long-lived alternate source trees such as `integration`, `validate`, `new-version`, or machine-specific “development copies” that can diverge from `main`.
4. **Parallel work stays isolated.** Concurrent developers use separate branches and worktrees. Never share one mutable working tree between tasks. If two tasks overlap, coordinate through the current `main` and explicit PR dependencies rather than copying files between workspaces.
5. **Avoid moving PR stacks.** If task B truly depends on task A, prefer finishing and merging A into `main`, refreshing from `main`, then continuing B. Do not leave a chain of PRs whose bases keep moving unless there is a concrete integration reason.
6. **Validate the exact candidate.** Run the relevant repository checks against the exact task head after refreshing its base. A green run on an older head or older base is not merge evidence.
7. **Merge back to online main.** Once the task satisfies its validation and review gates, merge it into `origin/main`. From that point, `main` is the product state; do not keep using the pre-merge task workspace as an alternate development line.
8. **Clean up immediately after integration.** Remove the local task worktree, delete the local task branch, delete the merged/closed remote task branch when safe, and prune stale worktree/remote-tracking metadata. A completed task must not leave an active development copy behind.
9. **Preserve before deleting uncertain work.** If an old workspace contains unique uncommitted or unpushed changes, inspect it before cleanup. Preserve genuinely unique material as a clearly named recovery artifact (for example a verified Git bundle) outside the active source tree, then remove the obsolete workspace. Recovery artifacts are cold backup only and must never be treated as a development source.
10. **Do not develop directly on `main` by default.** Use the task-branch flow even for small changes. Direct `main` maintenance is an explicit maintainer exception for a specific operation, not a standing shortcut.

Before consequential remote writes, refresh `origin/main` and overlapping PR state so another developer's merge cannot silently invalidate the base you verified. The desired steady state is always: one clean local `main`, zero or a small number of active task worktrees, and no unexplained duplicate Stardust source trees on developer machines.

## Development Environment

Activate the repository environment before Python work:

```bash
source .venv/bin/activate   # or: source venv/bin/activate
```

`scripts/run_tests.sh` probes `.venv`, then `venv`, then the managed compatibility location under `$HERMES_HOME`.

For JavaScript/TypeScript, install dependencies from the repository root. Workspace checks assume the root lockfile and workspace graph are authoritative.

## Project Structure

The filesystem is canonical; counts and exact module splits change over time. Important entry points include:

```text
run_agent.py          agent facade; turn loop implementation lives under agent/
model_tools.py        tool orchestration/discovery
 toolsets.py           named toolsets and core tool surface
cli.py                interactive CLI facade
hermes_state.py       session/state facade
hermes_constants.py   profile-aware home/path helpers
agent/                providers, turn phases, memory, compression, prompt assembly
hermes_cli/           CLI subcommands, config, setup, updater, dashboard backend
hermes_cli/web_routers/ dashboard API routers
tools/                tool implementations and environment backends
gateway/              messaging gateway and platform adapters
plugins/              plugin implementations and plugin contracts
skills/               built-in skills
ui-tui/               Ink terminal UI
tui_gateway/          JSON-RPC backend used by TUI/Desktop
apps/desktop/         Electron Stardust Desktop
apps/shared/          shared desktop/gateway protocol code
web/                  dashboard SPA
cron/                 scheduled jobs
scripts/              test/release/install/CI utilities
website/              Docusaurus documentation source
tests/                Python regression suite
```

User state is profile-aware under the compatibility `HERMES_HOME` hierarchy. Do not move or rename those directories without an explicit migration.

## Dependency Policy

Dependencies must remain bounded and reproducible.

- Python packages use an appropriate floor and upper bound; avoid bare unbounded `>=` constraints.
- Git dependencies pin immutable commit SHAs.
- GitHub Actions pin immutable action SHAs with a version comment.
- CI-only tools may use exact pins.
- Run `uv lock` after changing Python dependency declarations.
- Do not change dependency versions as collateral damage during unrelated refactors or branding work.

## Testing

For Python, use `scripts/run_tests.sh` rather than bare `pytest`. The wrapper enforces CI-like credential isolation, locale/timezone behavior, temporary `HERMES_HOME`, and file-level subprocess isolation.

```bash
scripts/run_tests.sh
scripts/run_tests.sh tests/gateway/
scripts/run_tests.sh tests/agent/test_foo.py -k test_x
```

General rules:

- A bug fix should normally add 1–2 invariant/behavior tests that fail on the broken behavior and pass on the fix.
- Test relationships and contracts, not volatile snapshots such as model counts, config version numbers, or catalog membership that is expected to change.
- Tests that depend on the real host OS run on that OS with the repository's `linux_only`, `macos_only`, or `windows_only` markers. Do not fake the interpreter's host platform to make process/filesystem behavior appear portable.
- Tests must not write personal state under the real home directory. Use the repository fixtures/temp `HERMES_HOME` patterns.
- Resolution chains, config propagation, security boundaries, remote backends, filesystem/network behavior, installers, and update paths need integration/E2E proof when practical; mocks alone are not enough.
- Prefer tests of behavior through callable/public seams. Source-text assertions are generally brittle; use them only for narrow repository-policy/source-authority invariants where executing every platform path is impractical, and pair them with runtime/integration coverage when the behavior is executable.
- Desktop visual/interaction changes require real Electron-window inspection for representative states and sizes in addition to static/type/unit checks.

### CI lane placement

`scripts/ci/classify_changes.py` decides which jobs run from changed paths. Put a regression test in a lane that will execute when the production source changes. A Python test that only asserts about TypeScript/package files may not run on a JS-only change; those checks usually belong in the JS/Vitest suite instead.

Do not weaken a check merely because historical failures are inconvenient. If a failure is unrelated, record the exact existing failure and keep the new contract independently proven.

## Commit and Review Discipline

- Preserve unrelated local/in-flight changes.
- Keep each commit coherent enough to review and revert.
- Before a large replacement, compare the resulting file/commit against the fresh base and verify no unrelated dependencies, generated files, or behavior changed.
- Do not force-push or rewrite shared history unless explicitly required by the maintainer.
- Direct `main` maintenance is allowed by project policy when authorized, but validation standards do not become weaker because there is no PR.

## Routing Table — working in X → read X/AGENTS.md

| Area | Read | Covers |
|---|---|---|
| `run_agent.py`, `agent/` | `agent/AGENTS.md` | turn phases, caching, providers, compression, message invariants |
| `cli.py`, `hermes_cli/`, `main.py` | `hermes_cli/AGENTS.md` | CLI, config, setup, profiles, updater, dashboard backend |
| `gateway/` | `gateway/AGENTS.md` | adapters, streaming, lifecycle, token/secrets boundaries |
| `tools/`, `toolsets.py`, `model_tools.py` | `tools/AGENTS.md` | tools, registry, toolsets, delegation, environment backends |
| `plugins/`, `hermes_cli/plugins*.py` | `plugins/AGENTS.md` | plugin contracts and compatibility |
| `tui_gateway/`, `ui-tui/` | `tui_gateway/AGENTS.md` | JSON-RPC process/transport and terminal UI |
| `web/`, `hermes_cli/web_routers/` | `web/AGENTS.md` | dashboard architecture and API/UI ownership |
| `apps/desktop/` | `apps/desktop/AGENTS.md`, `apps/desktop/src/AGENTS.md` | Stardust Desktop architecture, transport, state, packaging, UX |
| `skills/`, `optional-skills/`, curator code | `skills/AGENTS.md` | skill authoring and curation |
| `cron/`, kanban surfaces | `cron/AGENTS.md` | scheduler and kanban invariants |
| new `gateway/platforms/` adapter | `gateway/platforms/ADDING_A_PLATFORM.md` | platform adapter contract |

Long-form architectural background remains under `website/docs/developer-guide/`. Treat historical Hermes names there as technical history unless they falsely describe current Stardust product ownership, support, update, or release authority.
