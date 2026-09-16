# Active development claims

This file coordinates concurrent work on the Stardust fork. Keep claims narrow, name the expected write set, and remove or mark them complete when the work is merged or abandoned.

## Backend route failover reliability & operability

- **Owner:** ChatGPT backend reliability session
- **Status:** implementation complete; validation/review in progress
- **Started:** 2026-09-16
- **Branch:** `fix/backend-route-health-hardening`
- **PR:** #2 (draft)
- **Goal:** make persistent model-route health cross-process safe, observable, and safely resettable without issuing network/model probes.
- **Expected write set:**
  - `agent/route_health.py`
  - `hermes_cli/fallback_cmd.py`
  - `hermes_cli/subcommands/fallback.py`
  - `tests/agent/test_persistent_route_health.py`
  - `tests/agent/test_route_health_observability.py`
  - `tests/hermes_cli/test_fallback_health_cmd.py`
  - `IN_PROGRESS.md`
- **Protected / out of scope:**
  - `apps/desktop/**`
  - PR #1 / branch `feat/aurora-desktop-shell`
  - gateway protocol and delivery code
  - model request/response protocol
  - unrelated shared config or dependency manifests
- **Compatibility:** extend the existing `hermes fallback` surface; no new top-level command, core model tool, dependency, or persistent authority.
