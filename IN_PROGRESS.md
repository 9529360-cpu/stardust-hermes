# In Progress

## Stardust passive update policy

- Status: in progress
- Owner: ChatGPT / GPT-5.6 Sol
- Started: 2026-09-16T23:10:00+02:00
- Tracking: issue #5, item 2
- Branch: `fix/stardust-passive-update-policy`
- Scope:
  - `hermes_cli/config_defaults.py`
  - `cli-config.yaml.example`
  - `tests/hermes_cli/test_passive_update_opt_out.py`
- Contract:
  - Stardust passive/background upstream update checks default to off.
  - Users may explicitly opt in with `updates.check: true`.
  - Explicit/manual update checks remain available.
- Exclusions: `apps/desktop/**`, backend route-health work, A2A profile work, install-id locking, cron locking, and update-apply internals.
