# cron/ — scheduled jobs

Applies on top of the root `AGENTS.md`. Long-form: `website/docs/developer-guide/cron-internals.md`;
user docs `website/docs/user-guide/features/cron.md`.

## Cron

`cron/jobs.py` (job store) + `cron/scheduler.py` (tick loop, in-flight registry, `run_one_job`,
`python -m cron.scheduler` entry). `jobs.py` keeps the store itself (paths, locks, load/save,
record normalization, state predicates, run output, telemetry counters) and re-exports its
`jobs_*` siblings: `schedule` (schedule grammar, next run), `records` (create/edit/pause/resume/
remove), `runs` (run outcomes, dispatch/heartbeat/fire claims), `due` (repairs, catch-up,
`get_due_jobs`), `ticker` (liveness markers); they reach it late-bound (`_jobs.<name>`).
`scheduler.py` keeps the stateful parts and re-exports its
`scheduler_*` siblings: `job_runtime` (toolsets, model/runtime, pool, agent construction),
`agent_run` (prompt, watchdog, final response, `run_job`), `run_outcome` (compose/deliver/mark),
`external_worker`, `failures` (notices, incidents), `delivery`, `prompt`, `preflight`, `script`,
`provider`. Siblings reach it late-bound (`_sched.<name>`) so `monkeypatch.setattr(cron.scheduler,
...)` keeps reaching every caller; the `__main__` entry stays below every split-module import. Agents
schedule via the `cronjob` tool; users via `hermes cron list|add|edit|pause|resume|run|remove` or
`/cron`. Schedules: duration (`"30m"`, `"2h"`, `"1d"`), "every" phrase (`"every 2h"`, `"every monday
9am"`), 5-field cron (`"0 9 * * *"`), ISO one-shot (`"2026-06-01T09:00:00Z"`). Per-job fields:
`skills`, `model`/`provider` overrides, `script` (pre-run data-collection script whose stdout is
injected into the prompt; `no_agent=True` makes the script the whole job), `context_from` (chain job
A's last output into job B's prompt), `workdir` (run with that directory's `AGENTS.md`/`CLAUDE.md`
loaded), `stop_when_done` (opt-in goal-oriented recurring job that retires through the existing `completed` state only after a successful run emits a standalone `[DONE]` edge marker), multi-platform delivery, and `approval_mode` (`inherit|approve|deny`) for durable job-scoped approval authority. `approve` is a delegated capability: model-facing creation/update must obtain an explicit live human approval, while operator CLI flags count as direct consent. Transient YOLO/off posture and autonomous parent cron authority must never be converted into a new durable grant.

Session handoff: model-facing create/update with `attach_to_session=true` snapshots a bounded
recent user/assistant text tail from the owning profile's existing SessionDB into internal
`handoff_context`. Prompt assembly injects that fixed creation/update-time snapshot as runtime
background. It is not a live transcript link, never includes tool/system payloads, is not
echoed by cron list views, and disabling `attach_to_session` clears it. Snapshot failure must
not block scheduling; an explicit refresh that cannot capture a new snapshot clears any stale
handoff rather than presenting old conversation as newly refreshed context.

Hardening invariants — each guards a real failure; don't weaken without answering for it:
- **3-minute hard interrupt** on cron sessions: runaway loops cannot monopolise the scheduler.
- Catch-up window = half the period, clamped to 120s–2h; 120s grace for missed one-shots.
- Every recurring occurrence is accounted for: `tick()` advances `next_run_at` BEFORE dispatch
  (at-most-once across a mid-run crash) and stamps `pending_slot` in the same save; a scan that
  finds the stamp with a dead owner restores the instant ONCE (`cron/occurrences.py`), the
  executions ledger's `scheduled_instant` blocks a second fire, `cron.catch_up_missed: false`
  skips past-grace misses with a logged reason. Never drop a slot silently (#107485).
- File lock `~/.hermes/cron/.tick.lock` prevents duplicate ticks across processes.
- Cron sessions pass `skip_memory=True`; memory providers intentionally do not run during cron.
- `stop_when_done` is a scheduler-owned terminal transition, not self-deletion: only explicit opt-in
  jobs interpret `[DONE]`, the final result is delivered first, then `mark_job_run(...,
  terminal_complete=True)` reuses `_complete_job_record`. Delivery failure must not reschedule an
  already-satisfied real-world goal; preserve `last_status=delivery_failed` on the completed record.
- Cron execution has its own session. Eligible continuable deliveries may mirror or seed the
  reply-facing conversation: origin, origin-less home fallback, user-written bare-platform home,
  or opted-in explicit targets. `all` expansions do not gain home mirror eligibility. Mirrored
  briefs are labelled user turns appended at a turn boundary, preserving role alternation.
- The cron ticker runs in the desktop-spawned backend when `HERMES_DESKTOP=1` — that env var means
  "spawned by the app", not "a GUI is watching" (root: capability is a property of the session).
- Background `delegate_task` is process-local; work that must survive restarts is a cron job or a
  `terminal(background=True, notify_on_complete=True)` process.

## Tests

`tests/cron/`. Verify scheduling, ownership, delivery, cancellation, and restart behavior through the canonical test runner.
