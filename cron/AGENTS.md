# cron/ (+ kanban) — scheduled jobs and the multi-agent work queue

Applies on top of the root `AGENTS.md`. Long-form: `website/docs/developer-guide/cron-internals.md`;
user docs `website/docs/user-guide/features/cron.md`, `kanban.md`.

## Cron

`cron/jobs.py` (job store) + `cron/scheduler.py` (tick loop, in-flight registry, `run_one_job`,
`python -m cron.scheduler` entry). `scheduler.py` keeps the stateful parts and re-exports its
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

## Kanban (multi-agent work queue)

Durable SQLite-backed board letting multiple profiles/workers collaborate. Users: `hermes kanban
<verb>`; dispatcher-spawned workers use a dedicated `kanban_*` toolset so their schema footprint is
zero outside a kanban task (footprint ladder rung 3).

- **Storage:** `hermes_cli/kanban_db.py` is the facade (data classes, schema SQL, task creation,
  links, comments/events, runs, ready recompute) and re-exports its `kanban_db_*` siblings:
  `boards` (slugs, paths, board.json), `connect` (connections, schema init/migrations), `claims`,
  `completion`, `transitions` (block/review/unblock/reopen/schedule), `retirement`
  (archive/delete), `worker_context`, `maintenance` (stats, GC, logs, assignees), `dispatch`,
  `workspace`, `notify`, `graph`. Siblings reach the facade late-bound (`_kb.<name>`) so
  `monkeypatch.setattr(kanban_db, ...)` keeps reaching every caller; new code follows that
  convention.
- **CLI:** `hermes_cli/kanban.py` facade + `kanban_*.py` siblings (`boards`, `ops`, `parser`,
  `output`, `transfer`, `decompose`, `swarm`, ...). Verbs: `init, create, list (ls), show, assign, link,
  unlink, comment, attach, attachments, attach-rm, complete, request-review, request-changes,
  reopen-review, block, unblock, archive, tail`, plus `watch, stats, runs, log, assignees, heartbeat,
  notify-*, dispatch, daemon, gc`. Argparse alias dispatch must accept both `list` and `ls` (root).
- **Toolset:** `tools/kanban_tools.py` — `kanban_show, kanban_complete, kanban_request_review,
  kanban_request_changes, kanban_block, kanban_heartbeat, kanban_comment, kanban_create, kanban_link,
  kanban_attach, kanban_attach_url, kanban_attachments`; platforms whose saved selection enables
  `kanban` (`hermes tools enable kanban --platform <p>`; default-off, in `CONFIGURABLE_TOOLSETS`) get
  the full set plus `kanban_list`/`kanban_unblock` for board routing. The check_fn reads the schema
  build's own selection (`tools/kanban_toolset_context.py`), never the legacy top-level `toolsets`
  key alone.
- **Dispatcher:** long-lived loop (default 60s) that reclaims stale claims, promotes ready tasks,
  atomically claims, and spawns assigned profiles. Runs **inside the gateway** by default
  (`kanban.dispatch_in_gateway: true`). Standalone: `plugins/kanban/systemd/hermes-kanban-dispatcher.service`.
- **Plugin assets:** `plugins/kanban/dashboard/` (web UI) + systemd unit. `kanban_db.connect` is its
  own connection helper — do not alias it to `projects_db.connect` (a path-proximity generator did).

Isolation: **board** is the hard boundary — workers get `HERMES_KANBAN_BOARD` pinned in their env and
cannot see other boards; **tenant** is a soft namespace within a board (workspace-path + memory-key
isolation, one fleet serving several businesses). After `kanban.failure_limit` consecutive
non-success attempts on a task (default 2) the dispatcher auto-blocks it to stop spin loops.
Process-identity note: `kanban --preserve-cache` contains "serve" — never classify processes by argv
substring (root).

## Tests

`tests/cron/`, `tests/hermes_cli/test_kanban*.py`, `tests/tools/test_kanban*.py`. Schedule parsing
and catch-up windows are pure functions — test them as data. Never assert on the verb list or
toolset size (root: no change-detectors). Time-based tests use loose bounds (≥ 2s) and event sync.
