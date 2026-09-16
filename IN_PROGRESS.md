# In Progress

## Stardust pinned update policy

- Status: in progress
- Owner: ChatGPT / GPT-5.6 Sol
- Started: 2026-09-16T23:10:00+02:00
- Tracking: issue #5, item 2 plus red-team update-state review
- Branch: `fix/stardust-passive-update-policy`
- Scope:
  - `hermes_cli/banner.py`
  - `hermes_cli/update_contract.py`
  - `hermes_cli/web_routers/actions.py`
  - `tests/hermes_cli/test_passive_update_opt_out.py`
  - focused Stardust update-policy tests added by this branch
- Contract:
  - Stardust never performs automatic/passive upstream update checks.
  - Explicit/manual upstream comparison remains available.
  - Built-in in-place update apply is refused at the dashboard boundary instead of spawning a no-op child that can be misreported as success.
  - Dashboard background/default update status is passive; only an explicit forced check may contact upstream.
  - Stardust update status never advertises in-place apply capability.
- Exclusions: `apps/desktop/**`, backend route-health work, A2A profile work, install-id locking, cron locking, and the legacy upstream updater internals.
