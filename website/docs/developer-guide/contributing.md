---
sidebar_position: 4
title: "Development & Maintenance"
description: "Set up and maintain the independently managed Stardust codebase"
---

# Development & Maintenance

Stardust is maintained directly in `9529360-cpu/stardust-hermes`. This guide covers development setup and engineering expectations for this repository; it is not the upstream Hermes PR/contributor process.

Read the repository root [`AGENTS.md`](https://github.com/9529360-cpu/stardust-hermes/blob/main/AGENTS.md), [`STARDUST.md`](https://github.com/9529360-cpu/stardust-hermes/blob/main/STARDUST.md), and the nearest area-specific `AGENTS.md` before making substantial changes.

## Maintenance priorities

1. Correctness, data safety, privacy, security, and recoverability.
2. A reliable long-lived personal-assistant desktop and daily workflow.
3. Stable model routing, credentials, health tracking, and graceful failover.
4. Clear Stardust-owned install, update, recovery, and release paths.
5. Cross-platform reliability for the inherited runtime surfaces that Stardust still uses.
6. Focused regression coverage at the boundary that changed.
7. Compatibility with inherited `hermes` interfaces where renaming would create migration risk without user value.

Stardust does not perform periodic upstream merge/rebase or automatic synchronization with `NousResearch/hermes-agent`. A useful upstream implementation may be manually studied or adapted for a concrete need, but the default development path is this repository.

## Development Setup

### Prerequisites

| Requirement          | Notes                                                                                         |
| -------------------- | --------------------------------------------------------------------------------------------- |
| **Git**              | Git LFS is useful for workflows that touch LFS-managed assets                                 |
| **Python 3.11–3.13** | The managed installer can provision a supported interpreter                                   |
| **uv**               | Python package/environment manager                                                             |
| **Node.js**          | Use a version accepted by the root workspace `engines` field                                  |

### Option 1: Stardust managed layout

For development that should match the installed runtime layout, start with the Stardust bootstrap:

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
cd "${HERMES_HOME:-$HOME/.hermes}/hermes-agent"

uv pip install -e ".[all,dev]"
npm install
```

The checkout directory and `hermes` command keep inherited names for compatibility. Product/source ownership remains Stardust.

### Option 2: Manual clone

For an isolated source checkout or CI-style workspace:

```bash
git clone https://github.com/9529360-cpu/stardust-hermes.git
cd stardust-hermes

uv venv ~/.hermes/venvs/stardust-dev --python 3.11
export VIRTUAL_ENV="$HOME/.hermes/venvs/stardust-dev"
export PATH="$VIRTUAL_ENV/bin:$PATH"

uv pip install -e ".[all,dev]"
npm install
```

Keeping the venv outside the working tree reduces the risk that a workspace-relative cleanup command deletes the interpreter currently running the agent.

### Development config

Do not put real credentials in the repository. Use an isolated `HERMES_HOME` or the repository's development sandbox helpers when possible.

```bash
scripts/dev-sandbox.sh python -m hermes_cli.main
scripts/dev-sandbox.sh --persistent python -m hermes_cli.main desktop
```

For a normal local runtime, configuration still lives under the inherited profile-aware `HERMES_HOME` hierarchy.

### Run

```bash
hermes doctor
hermes chat -q "Hello"
hermes desktop
```

`hermes` is the compatibility CLI name used by Stardust's inherited runtime.

## Tests

For Python, use the repository wrapper rather than bare pytest unless you are deliberately debugging outside the CI-like harness:

```bash
scripts/run_tests.sh
scripts/run_tests.sh tests/hermes_cli/
```

For Desktop work, follow [`apps/desktop/README.md`](https://github.com/9529360-cpu/stardust-hermes/blob/main/apps/desktop/README.md) and the Desktop `AGENTS.md`. Typical checks include:

```bash
cd apps/desktop
npm run typecheck
npm run lint
npm run test:ui
npm run test:desktop:platforms
```

Visual/interaction changes also require inspection in a real Electron window; compilation alone is not proof of the requested experience.

## Code and architecture rules

The root `AGENTS.md` is the canonical engineering guide. In particular:

- preserve per-conversation prompt-cache invariants;
- keep the core tool waist narrow and grow capabilities at the edges;
- put state with the subsystem that is authoritative for it;
- do not add speculative fallback layers or flags without a real consumer/failure mode;
- never hardcode the runtime home for new state; use the canonical profile-aware path helpers;
- keep `.env` for secrets and structured config for non-secret behavior;
- do not mechanically rename inherited compatibility identifiers such as `hermes`, `HERMES_HOME`, `window.hermesDesktop`, application IDs, protocol names, or data paths;
- dependency version changes must not leak into unrelated branding/refactor work.

## Cross-platform compatibility

Native Windows, macOS, Linux, WSL, containerized deployments, and remote Gateway operation have different process/filesystem semantics. If a change touches subprocesses, signals, paths, native modules, service lifecycle, or installers, exercise the relevant platform-specific test lane rather than pretending another host is Windows/macOS through mocks.

Common rules:

- avoid unguarded POSIX-only process primitives on Windows;
- catch the actual platform-specific process errors;
- open text files with explicit UTF-8 when the repository contract expects UTF-8;
- use `pathlib.Path` / `os.path` rather than manually constructing filesystem separators;
- do not assume Desktop and backend run on the same machine.

## Security

Stardust inherits an agent runtime with terminal, filesystem, browser, gateway, plugin, and credential capabilities. Security-sensitive changes should preserve the existing approval, path-hardening, secret-redaction, process-isolation, and plugin/tool boundaries.

Never log or commit real secrets. Resolve filesystem authorization using canonicalized paths and preserve the distinction between user intent, untrusted model/tool input, and authoritative local state.

## Branches, commits, and review

Direct `main` maintenance is allowed when explicitly authorized by the maintainer, but the validation bar does not become weaker. Keep commits coherent and reversible, preserve unrelated in-flight work, and inspect large full-file replacements for collateral changes before treating them as complete.

Conventional commit prefixes are useful for readable history:

```text
fix: ...
feat: ...
docs: ...
test: ...
refactor: ...
chore: ...
```

If external contributors are accepted in the future, normal GitHub branches/PRs may be used. Repository CI includes attribution protection for real outside authors, while the owner remains first-party maintenance.

## Reporting issues

Use the Stardust repository issue tracker:

[github.com/9529360-cpu/stardust-hermes/issues](https://github.com/9529360-cpu/stardust-hermes/issues)

Include the OS, relevant runtime version, reproduction steps, and full non-secret error output. Do not attach credentials, `.env` files, personal conversations, profile exports containing private data, or local databases.

## Technical foundation and license

Stardust was built from the open-source Hermes Agent codebase and retains its original Git history, copyright notices, and MIT License. References to upstream Hermes are appropriate when they document code origin, history, or a real external integration; they should not imply upstream product ownership of Stardust.
