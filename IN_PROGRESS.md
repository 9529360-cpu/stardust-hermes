# In Progress

## E-stop resume integrity

- Status: in progress
- Owner: ChatGPT / GPT-5.6 Sol
- Started: 2026-09-16T23:45:00+02:00
- Tracking: issue #5, item 1
- Branch: `fix/estop-resume-integrity`
- Scope:
  - `agent/estop.py`
  - `hermes_cli/subcommands/pause.py`
  - the smallest gateway `/pause off` owner required for semantic parity
  - focused E-stop/pause regressions
- Contract:
  - no visible sentinel -> explicit no-op/not-paused result
  - all visible sentinels removed -> resumed
  - any unlink failure or any remaining sentinel -> incomplete-resume failure, never success
  - CLI and Gateway render the same authoritative result semantics
- Exclusions: desktop, update policy, route-health, A2A, install identity, cron locking.
