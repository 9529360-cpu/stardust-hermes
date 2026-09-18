# Backend Continuation Handoff — 2026-09-18

## 1. Resume here

Continue backend/runtime ownership for `stardust-hermes`.

- Backend machine: `DESKTOP-KKER56V`
- Backend device id: `e9eee2af-398b-4829-ad0e-86354427839c`
- Repo: `D:\远程工作区\stardust-hermes`
- Branch: `dev/stardust-backend-runtime`
- HEAD at handoff: `0c71846e37873c6144f93dda91377dd7413909dd`
- Frontend machine `豹` is a separate parallel worktree. Do not use it for backend mutations.
- Read root `AGENTS.md` and `tui_gateway/AGENTS.md` before changes.
- Also read the prior `HANDOFF_BACKEND_2026-09-18.md` for older context.

## 2. Commit policy

Do **not** commit or push yet.

The user explicitly chose: continue backend work, then organize clean milestone commits later.
The tree has many staged + unstaged changes from ongoing parallel backend work.
Do not reset, clean, stash, rebase, or discard anything.
Do not assume `git status` dirt is yours.

Current status includes many `M`, `MM`, `AM` entries plus untracked Backend Lab scripts/docs.
## 3. Work completed in this continuation

### A. Isolated compute-host/orphan regression cleanup

The previous Windows orphan/EOF failures are now closed.

Key production/runtime behavior already present in the worktree:
- Windows compute-host stdin uses low-level `os.read` control-line iteration to avoid TextIO pipe EOF races.
- compute-host activity is fenced by per-turn activity generation so old activity cannot make a new turn look fresh.
- real orphan interrupt must be acknowledged without host PID/generation replacement.
- Windows SIGTERM flush test calls the installed handler directly because `os.kill(SIGTERM)` terminates instead of exercising Python signal delivery.
- symlink test skips WinError 1314 only when Windows lacks symlink privilege.

The noisy orphan diagnostic scaffolding was reduced to durable behavioral assertions.
`tests/tui_gateway/test_isolated_orphan_activity.py` remained 10/10 green after cleanup.

### B. Compute-host terminal crash recovery bug fixed

Found a real bug: after supervisor synthesized terminal `turn.error(reason="crash")`, the parent UI showed a terminal error but the generic crash marker could remain.
A later resume could auto-continue/replay a turn whose side effects were already unknown.

Fix in `tui_gateway/compute_host_bridge.py`:
- crash-terminal errors preserve failed `inflight_turn` so reconnect can still show the failure;
- ordinary sessions retire the generic auto-continue marker after terminal crash error;
- `bot_room` sessions keep both crash marker + marker identity because the durable hosted-room task/receipt driver owns recovery.

Added regressions in `tests/tui_gateway/test_auto_continue.py` for both ordinary and hosted-room ownership.
### C. Todo portability edge verified and pinned

Checked the low-coverage imported-session path using a real `SessionDB` round trip.
`sessions.model_config["_todo_state"]` including Todo revision survives export/import intact.

Added:
`test_import_sessions_preserves_session_scoped_todo_state`
in `tests/hermes_state/test_hermes_state.py`.

Profile adoption already has an existing regression in:
`tests/tui_gateway/test_stranded_session_adoption.py`
that asserts `_todo_state` moves with the adopted session.

No production import/adoption change was needed.

## 4. Fresh validation already run

All of these were green after the crash-terminal fix:

- targeted crash-terminal tests: 2 passed
- auto-continue + compute-host group: 65 passed, 0 failed
- hosted-room lifecycle group: 123 passed, 0 failed
- Todo import round-trip regression: passed
- Ruff on touched crash-terminal files: passed
- `git diff --check`: passed
- Backend Lab `fast / compute-host`: green
- Backend Lab `deep / timeline`: green
- Backend Lab `deep / hosted-room`: green
- Backend Lab `deep / agent-core`: green

Backend Lab latest/current state was idle and green at handoff.
Artifacts live under `D:\远程工作区\artifacts\backend-lab`.
## 5. Current active investigation — continue this first

A new lifecycle gap was identified but **not fixed yet**:

`session.close` in `tui_gateway/methods_session.py` currently does:

`_pop_session_by_id(...) -> _teardown_popped_session(...)`

It does **not** call `_interrupt_session_turn()` first.

For normal inline turns, `_teardown_popped_session` waits on `_run_thread`.
For compute-host isolated turns, the parent has no local live `_run_thread` to wait on.
Therefore an explicit `session.close` can pop/finalize the parent session while the child compute-host turn is still running and potentially performing side effects.

This is more serious than the previously reviewed `HostSupervisor.shutdown()` path.
`HostSupervisor.shutdown()` appears process-exit/test-oriented and not a normal runtime hot-restart API, so do not change it without new evidence.

### Intended invariant for session.close

Once explicit `session.close` returns success, a live isolated turn must not continue executing in the child.

Likely safe shape to prove before implementation:
1. claim/pop the session under the existing resume ownership lock;
2. if it is a live compute-host session, send interrupt with `wait=True`;
3. require positive `interrupt.ack(applied=True)`;
4. wait a bounded interval for the real terminal frame to make parent `running=False`;
5. only then finalize/close resources;
6. preserve hosted-room/durable recovery ownership semantics.

Do not blindly reuse fire-and-forget orphan-reaper semantics for explicit close.
## 6. Evidence around the open session.close issue

Compute-host child interrupt semantics:
- `compute_host._handle_interrupt` looks up child-local session;
- calls child `server._interrupt_session_turn(sid, session)`;
- replies `interrupt.ack(applied=True)` after the child accepted/applied cancellation.

Important: the ack proves cancellation was applied, not necessarily that the turn thread has fully unwound.
The terminal `turn.end/turn.error` is the stronger settle signal.

Existing related tests:
- `test_session_close_settles_active_turn_before_teardown` covers inline/local `_run_thread`, not compute-host isolation.
- `test_ws_orphan_reap_interrupts_isolated_turn_then_reaps` covers detached WS orphan cleanup with fire-and-forget interrupt and delayed reap.
- explicit `session.close` currently has no equivalent isolated-turn regression.

Start by adding a failing regression for explicit close of a running compute-host session before production edits.

## 7. Working-tree caution

The unstaged diff alone is already large (~15 files, ~726 insertions / 68 deletions at handoff), and many other files are staged.
Relevant currently modified files include:
- `tui_gateway/compute_host.py`
- `tui_gateway/compute_host_bridge.py`
- `tui_gateway/host_supervisor.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/session_lifecycle.py`
- `tests/tui_gateway/test_auto_continue.py`
- `tests/tui_gateway/test_compute_host_phase1.py`
- `tests/tui_gateway/test_tui_gateway_server.py`
- `tests/hermes_state/test_hermes_state.py`

Inspect both index and worktree before editing because several are `MM`.
## 8. Suggested first commands in the new conversation

Use Remote Desktop Commander on `DESKTOP-KKER56V`.

Read:
- this file
- `AGENTS.md`
- `tui_gateway/AGENTS.md`

Then inspect:
`git status --short --branch`
`git diff -- tui_gateway/methods_session.py tui_gateway/session_lifecycle.py tests/tui_gateway/test_tui_gateway_server.py`
`git diff --cached -- tui_gateway/methods_session.py tui_gateway/session_lifecycle.py tests/tui_gateway/test_tui_gateway_server.py`

Then reproduce/pin the isolated `session.close` lifecycle bug before patching.

After the fix, rerun at minimum:
- the new explicit-close isolated-turn regression
- session close / interrupt tests in `test_tui_gateway_server.py`
- compute-host phase/turn protocol tests
- auto-continue tests
- hosted-room lifecycle group
- `git diff --check`
- Ruff for touched files
- Backend Lab fast, then relevant deep pack(s)

Do not commit or push unless the user explicitly changes the current milestone policy.
