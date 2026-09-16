# In Progress

## Stardust release authority

- Status: in progress
- Owner: ChatGPT / GPT-5.6 Sol
- Started: 2026-09-16
- Branch: `feat/stardust-release-authority`
- Product contract:
  - `9529360-cpu/stardust-hermes` is the only automatic update and release authority for Stardust.
  - User-facing update checks and apply flows must target this repository, never `NousResearch/hermes-agent`.
  - NousResearch remains attribution/manual-reference upstream only; importing upstream changes is a deliberate maintainer operation.
  - GitHub Releases, tags, release notes, installers, and update metadata are published from this repository.
  - No release is published merely by merging this branch; publishing remains an explicit release action.
- Scope:
  - Stardust release workflow and release documentation
  - update-source authority seams outside `apps/desktop/**`
  - focused regressions for repository identity where practical
- Protected / excluded write sets:
  - `apps/desktop/**` (PR #1 / #6 ownership)
  - route health (PR #2)
  - A2A profile work (PR #3)
  - install-id locking (PR #4)
  - cron locking
