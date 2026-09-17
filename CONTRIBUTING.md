# Maintaining Stardust

Stardust is an independently maintained personal-assistant project built on the Hermes Agent codebase. This file describes how work in **this repository** should be approached; it is not the upstream Hermes contribution guide.

The source and release authority is:

`9529360-cpu/stardust-hermes`

See [`STARDUST.md`](STARDUST.md) for the product, privacy, source-authority, and upstream policies.

## Maintenance direction

Changes should move Stardust toward a durable personal assistant rather than preserve upstream product identity or release cadence.

Priorities are:

1. Correctness, data safety, security, and recoverability.
2. A reliable personal-assistant desktop and daily workflow.
3. Stable model routing, credential rotation, health tracking, and graceful failover.
4. Fast, observable startup, connection switching, and remote/local operation.
5. Focused regression coverage at the boundary that changed.
6. Clear Stardust-owned install, update, recovery, and release paths.
7. Compatibility with inherited internal interfaces where renaming would create risk without user value.

## Upstream policy

Do not treat `NousResearch/hermes-agent` as a branch that Stardust must continuously follow.

- No periodic upstream merge or rebase is part of normal maintenance.
- No automated upstream synchronization should be introduced.
- No installer, updater, recovery path, or release workflow may silently replace Stardust source with an upstream checkout.
- A specific upstream implementation may be studied or manually adapted when useful, but that is an isolated engineering decision, not restoration of an upstream tracking relationship.
- Keep attribution, license notices, and useful Git history intact.

## Compatibility naming

A large amount of inherited runtime surface is named `hermes`: Python packages, the `hermes` CLI, `HERMES_HOME`, environment variables, protocol/IPC identifiers, install paths, and historical data directories.

Do **not** mechanically rename these just to make a grep result cleaner. A compatibility rename must include migration behavior, rollback/recovery considerations, and tests for existing installs.

User-facing product copy, repository ownership, new release/update URLs, new documentation, and newly introduced product identifiers should use **Stardust** unless a compatibility boundary requires otherwise.

## Development workflow

Before changing an area:

1. Read the root [`AGENTS.md`](AGENTS.md).
2. Read the nearest area-specific `AGENTS.md`.
3. Trace the real runtime path before deciding where to patch.
4. Reproduce the behavior or identify the exact contract being changed.
5. Preserve unrelated local or in-flight changes.
6. Validate the narrowest affected boundary first, then the broader integration boundary when practical.

For Python work, use the repository virtual environment and the existing test harness. For JavaScript/TypeScript work, install workspace dependencies from the repository root before running package checks.

Desktop work should normally include the checks documented in [`apps/desktop/README.md`](apps/desktop/README.md) and [`apps/desktop/AGENTS.md`](apps/desktop/AGENTS.md). Install/update/packaging work needs the release-path tests rather than renderer-only tests.

## Validation expectations

A change is not complete because it compiles.

- Bug fixes should have a regression test when the behavior is practical to automate.
- Config, resolution, routing, filesystem, auth, update, and remote/local changes should exercise the real boundary rather than only mocks.
- Desktop visual or interaction changes need real Electron-window inspection for representative states and sizes in addition to static checks.
- Installer and recovery changes must prove the source authority remains Stardust.
- Docker changes should pass the Stardust-owned smoke workflow and must not require upstream Docker Hub publishing credentials.

If an existing check is failing for unrelated historical reasons, record the exact failure and do not hide it by weakening the check.

## Privacy and repository hygiene

Do not commit personal runtime data. In particular, keep out:

- `.env`, API keys, tokens, credentials, private keys;
- local operator configuration;
- conversations, memories, profile exports, pairing state, logs, or caches;
- route-health/runtime state;
- SQLite/database files or backups;
- packaged desktop applications containing local state.

The `.gitignore` is a guardrail, not permission to skip review of a diff before publishing it.

## Release and update ownership

New release metadata, update checks, installer bootstrap URLs, recovery URLs, documentation links, and package repository fields must point to `9529360-cpu/stardust-hermes` unless the maintainer explicitly chooses another Stardust-owned endpoint.

The inherited `hermes update` upstream synchronization path is intentionally disabled in Stardust. Desktop product update affordances must not re-enable it indirectly.

## Historical references

Old issue numbers, commit messages, implementation comments, provider integrations, package names, and attribution may still mention Hermes or Nous Research. Keep them when they explain code history or identify a real external service. Remove or rewrite them when they falsely imply that this repository is still an upstream-managed product, release, support channel, or synchronization target.

## License

Stardust retains the repository's MIT License and original attribution/history. See [`LICENSE`](LICENSE) and the attribution section in [`STARDUST.md`](STARDUST.md).
