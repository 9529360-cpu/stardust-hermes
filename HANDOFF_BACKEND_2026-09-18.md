# Stardust Hermes Backend/Runtime Handoff — 2026-09-18

> **Authoritative handoff for the next backend/runtime conversation.**
> Continue from the real backend working tree. Do not re-clone, do not reset/clean/stash the whole tree, do not redo broad architecture research, and do not overwrite parallel work.

## 0. Machine truth — read this first

- **Backend machine:** `DESKTOP-KKER56V`
- **Remote Desktop Commander device id:** `e9eee2af-398b-4829-ad0e-86354427839c`
- **Backend repository:** `D:\远程工作区\stardust-hermes`
- **Branch:** `dev/stardust-backend-runtime`
- **HEAD observed this session:** `0c71846e37873c6144f93dda91377dd7413909dd`
- **Do NOT use “豹” for backend work.** “豹” is the frontend/Electron/UI machine.
- The older version of this handoff incorrectly contained “豹” in its environment/bootstrap text. That field was stale.
- The machine-split coordination file was observed on 豹 and explicitly says: 豹 = frontend, DESKTOP-KKER56V = backend.
- This handoff file and Backend Lab evidence are present on DESKTOP-KKER56V.

No commit, push, release, reset, clean, or whole-tree stash was performed in this conversation.

## 1. Required startup sequence next time

1. Connect to `DESKTOP-KKER56V`.
2. Read this file completely.
3. Read root `AGENTS.md` and `tui_gateway/AGENTS.md`; read `agent/AGENTS.md` before touching agent tests/code.
4. Refresh `git status --short --branch` and HEAD.
5. Read Backend Lab `latest.json` and `needs_attention.json`.
6. Continue the orphan/HostSupervisor investigation first; do not restart architecture discovery.

## 2. Shared working tree / ownership boundary

The backend tree is intentionally very dirty with staged, unstaged, and untracked work from multiple backend efforts.
Preserve unrelated changes. Do not replace whole files unless absolutely necessary.

Relevant mixed-state files observed:

- `AM tests/tui_gateway/_isolated_orphan_activity_child.py`
- `MM tests/tui_gateway/test_isolated_orphan_activity.py`
- `MM tests/tui_gateway/test_serve_exit_flush.py`
- ` M tui_gateway/compute_host.py`
- `M  tui_gateway/host_supervisor.py`
- `M  tui_gateway/session_lifecycle.py`
- `M  tui_gateway/session_reaper.py`
- many existing Agent/Todo/hosted-room changes are staged or modified
- untracked Backend Lab scripts/docs are expected
- untracked `scripts/_probe_compute_host_pid.py` and `scripts/_probe_orphan_child_stdin.py` are diagnostic work; do not delete blindly

Do not touch frontend/Electron files from the backend machine unless the user explicitly changes scope.

## 3. Backend Lab remains the final oracle

Evidence root:

`D:\远程工作区\artifacts\backend-lab`

Important files:

- `latest.json`
- `needs_attention.json`
- `current.json`
- `latest-deep-*.json`
- `runs\...`
- `history\...`

Fast green must never erase an unresolved Deep red.

## 4. Latest Backend Lab state observed

Latest Fast run:

- profile: `fast`
- pack: `compute-host`
- finished: `2026-09-18T11:28:16Z`
- Ruff: pass
- tests: pass
- diff-check: pass
- Allure generation: pass
- summary: **203 passed, 0 failed, 4 skipped**
- `overall_ok`: **false**

That aggregate false is correct because Deep failures remain open.

Open Deep failures in `needs_attention.json`:

1. `deep/agent-core`
   - `tests/agent/test_tool_batch_segmentation.py::TestPathCanonicalization::test_symlink_aliases_are_not_parallelized`
   - Windows `Path.symlink_to()` fails with **WinError 1314**
   - treat as environment capability unless contrary evidence appears
   - do not weaken path canonicalization logic

2. `deep/timeline`
   - historical ledger still records the isolated-orphan failures
   - current focused evidence is newer and more discriminating than that old ledger tail
   - do not consider timeline closed until a new Deep timeline pack turns green

## 5. Production changes already present before this handoff

Keep these unless new evidence proves them wrong:

- `HostSupervisor._host_generation` process-generation fence
- stale stdout-reader frames are dropped by source generation
- failed-spawn generation is retired transactionally
- stdout drain is joined before exit is classified as crash
- pending controls are failed promptly when a host really dies
- compute-host activity now fences reused-agent activity with `_turn_liveness_activity_generation`
- explicit user Stop can wait for real `interrupt.ack`
- orphan/background cleanup remains fire-and-forget

## 6. Main runtime bug: isolated orphan activity

Primary test:

`tests/tui_gateway/test_isolated_orphan_activity.py::test_real_child_detached_turn_activity`

Real child helper:

`tests/tui_gateway/_isolated_orphan_activity_child.py`

The test intentionally exercises:

- real `HostSupervisor`
- real subprocess pipes
- real `ComputeHost`
- production activity sampling
- production parent activity relay/fencing
- production orphan-reaper interrupt path

Only provider/UI-heavy behavior is stubbed.

### 6.1 Previous-turn activity fence

The unstaged `tui_gateway/compute_host.py` change captures the agent's activity generation at turn start and only accepts activity if the generation advances.
Timestamp fallback remains only when the generation counter is unavailable.

Earlier `previous` runs now get past the stale-activity assertion.
Do not remove this generation fence while debugging the later interrupt lifecycle.

## 7. New evidence from this conversation — host replacement hypothesis was NOT reproduced

Focused command run:

`./scripts/run_tests.sh tests/tui_gateway/test_isolated_orphan_activity.py -k previous -q -s`

Result: failed, but with decisive diagnostics.

At the moment the parent sent the orphan interrupt:

- child hello PID: **19760**
- supervisor PID: **19760**
- Popen object id stayed the same
- stdin object id stayed the same
- `proc.poll()` was `None` before and after the interrupt write
- parent-side stdin reported `closed=False`
- `supervisor.is_running()` was true
- only one hello was observed

Therefore, in this run, the interrupt was **not** written to a replaced host generation.

## 8. New evidence — child stdin sees EOF while parent still owns an open pipe

The same focused run recorded:

- child frames: only `turn.start`
- `stdin-next-1-entered=True`
- `stdin-next-1-line=True`
- `stdin-next-2-entered=True`
- `stdin-next-2-line=False`
- `stdin-next-2-eof=True`
- child shutdown reason: `stdin_closed`
- parent later recorded sending `interrupt`
- child never recorded the interrupt frame

This is the current highest-value contradiction:

> the child control reader receives the first frame, then sees EOF on its second stdin read, while the parent still reports the same live Popen, same PID, same stdin object, open pipe, and `poll() is None`.

Do not patch plugin hooks or agent interrupt code yet. The interrupt never reached `ComputeHost.handle_frame` in this reproduction.

## 9. Runner hypothesis was also ruled out

The same `previous` case was run directly with pytest, bypassing `scripts/run_tests.sh` and the per-file runner:

`.venv\Scripts\python.exe -m pytest tests\tui_gateway\test_isolated_orphan_activity.py -k previous -q -s`

It reproduced the same signature:

- same PID before/after parent interrupt write
- child only saw `turn.start`
- child second stdin read returned EOF
- child shutdown reason `stdin_closed`
- parent pipe still appeared open/live

So this is **not specific to** `run_tests_parallel.py`.

## 10. Standalone probes — important contrast

Existing probe:

`scripts/_probe_orphan_child_stdin.py`

Run outside pytest with the venv launcher:

- Popen PID: **10864**
- hello host PID: **14116**
- host remained running
- child frames after 3s: `['turn.start']`
- no unexpected `stdin_closed`

The PID mismatch proves the Windows venv launcher can be an intermediate process.
This is why the integration test was changed to `sys._base_executable`.A scratch copy of the same probe was then run with `sys._base_executable` plus the venv site-packages on `PYTHONPATH`:

- Popen PID: **21636**
- hello host PID: **21636**
- host remained running
- child frames after 3s: `['turn.start']`
- no unexpected `stdin_closed`

Therefore:

- the venv launcher PID mismatch is real, but it is not the current stdin-EOF root cause
- `sys._base_executable` itself does not reproduce the EOF outside pytest
- pytest context/test lifecycle still matters somehow, but the canonical live-system subprocess guard is not the obvious cause

## 11. Pytest live-system guard check

The test is marked:

`@pytest.mark.live_system_guard_bypass`

`tests/conftest.py::_live_system_guard` immediately yields and returns when that marker is present.

Therefore its normal wrappers around:

- `subprocess.Popen`
- `subprocess.run`
- `os.kill`
- process-killer commands

are **not active** for this test.

Do not spend another cycle blaming that wrapper unless new evidence contradicts this.

## 12. Best next experiment

Stay narrow. The next question is no longer “did HostSupervisor respawn before interrupt?” for the reproduced `previous` case.

The next question is:

> Who closes, invalidates, or causes EOF on the child stdin read end after the first `turn.start` while the parent write end still appears open?

Recommended evidence to add temporarily:

- child-side Windows stdin OS handle value before first and second reads
- parent-side stdin pipe OS handle value before submit and before interrupt
- process PID + host generation alongside those handle values
- hook/patch any Python-level `proc.stdin.close()` / stream close seam if practical
- inspect whether a duplicate/launcher handle rather than the actual child's pipe is being observed
- keep the evidence inside the existing real-child test/probe; do not globally increase timeoutsOnly after the owner of EOF is proven should production code change.

Potential outcomes:

- if parent is closing the real write handle: fix that lifecycle owner
- if Windows handle inheritance/launcher topology creates a false live Popen: fix HostSupervisor process ownership/identity
- if the test fixture itself causes a synthetic EOF not possible in production: repair the integration harness without weakening the real-child contract
- if child run loop treats transient/read-handle state as permanent EOF incorrectly: fix `run_host` narrowly and add a Windows regression

Do not invent a second task/process state machine.

## 13. Fresh-mode second dispatch still needs proof after EOF issue

Older diagnostics saw a later `hello` around the fresh second-turn path and suggested respawn.
That remains worth retesting, but only after the current stdin EOF issue is understood.

For the fresh second dispatch, capture permanently or diagnostically:

- PID and `_host_generation` before first turn
- hello PID / boot_id
- PID/generation after first terminal frame
- PID/generation immediately before second `submit_turn`
- all second-turn hello/turn.started/turn.end/turn.error frames

Do not assume the older “new hello” observation is still the same root cause as the current `previous` failure.

## 14. serve_exit_flush remains second priority

Current unstaged test distinction in:

`tests/tui_gateway/test_serve_exit_flush.py`

On Windows:

- do **not** use `os.kill(os.getpid(), SIGTERM)` as a POSIX-style signal-delivery test
- Windows `os.kill(..., SIGTERM)` terminates rather than exercising Python's SIGTERM handler normally
- call `server._handle_exit_flush_signal(signal.SIGTERM, None)` directly on Windows
- keep real signal delivery on POSIX

Current staged production change in `session_reaper.py` also avoids starting a finalization worker thread when there are no sessions.

Run the focused file after orphan work is stable.

## 15. agent-core WinError 1314 remains third priority

Failing test:

`tests/agent/test_tool_batch_segmentation.py::TestPathCanonicalization::test_symlink_aliases_are_not_parallelized`

Current failure is Windows symlink privilege/capability:

`OSError: [WinError 1314] A required privilege is not held by the client`

Correct resolution:

- preserve real symlink overlap coverage on capable environments
- on incapable Windows environments, explicitly skip/xfail based on actual symlink capability
- do not alter canonicalization behavior to “make Windows green”
- prefer a small reusable capability probe only if the test suite already has a suitable convention

Then rerun the relevant agent-core Deep pack.

## 16. Validation order for the next conversation

1. Re-read current target files and focused diff.
2. Reproduce `-k previous` once with existing diagnostics.
3. Add only the minimal handle/close evidence needed to identify the stdin EOF owner.
4. Make the smallest production or harness fix supported by that evidence.
5. Run the full isolated-orphan file.
6. Run compute-host focused trio if HostSupervisor/compute_host changed.
7. Run `test_serve_exit_flush.py`.
8. Resolve symlink capability handling and run its focused test.
9. Let Backend Lab rerun.
10. Treat the issue closed only when `needs_attention.json` no longer contains the corresponding Deep failure.

Fast green alone is not closure.

## 17. Useful commands

Focused orphan:

`D:\DevTools\Git\bin\bash.exe -lc "cd /d/远程工作区/stardust-hermes && ./scripts/run_tests.sh tests/tui_gateway/test_isolated_orphan_activity.py -k previous -q -s"`

Full orphan:

`D:\DevTools\Git\bin\bash.exe -lc "cd /d/远程工作区/stardust-hermes && ./scripts/run_tests.sh tests/tui_gateway/test_isolated_orphan_activity.py -q"`

Direct pytest discriminator:

`D:\远程工作区\stardust-hermes\.venv\Scripts\python.exe -m pytest tests\tui_gateway\test_isolated_orphan_activity.py -k previous -q -s`

Backend Lab fast:

`D:\远程工作区\stardust-hermes\.venv\Scripts\python.exe D:\远程工作区\stardust-hermes\scripts\backend_lab.py fast`

## 18. Do not do

- Do not switch backend work back to 豹 because an old prompt says so.
- Do not clone again.
- Do not reset/clean/stash the shared backend tree.
- Do not delete diagnostic probes until the root cause is proven.
- Do not remove the activity-generation fence.
- Do not weaken explicit Stop's ack semantics.
- Do not merge explicit user Stop and orphan cleanup semantics.
- Do not patch plugin-hook timeouts without proof the interrupt frame reached the child.
- Do not “fix” WinError 1314 by weakening canonical path behavior.
- Do not declare success from Fast while Deep ledger remains red.
- Do not commit/push/release unless explicitly asked.

## 19. Exact bootstrap prompt for the next chat

> 继续 Stardust Hermes 后端/runtime 开发。使用 Veteran Full-Stack Engineer 和 Remote Desktop Commander，连接 Windows 设备 **DESKTOP-KKER56V**（device id `e9eee2af-398b-4829-ad0e-86354427839c`），工作目录 `D:\远程工作区\stardust-hermes`。先完整读取 `HANDOFF_BACKEND_2026-09-18.md`，以它为最新 authoritative 后端交接；再读取根 `AGENTS.md`、`tui_gateway/AGENTS.md`、当前 git status，以及 Backend Lab 的 `latest.json / needs_attention.json`。不要重新 clone，不要大范围架构调研，不要覆盖并行修改。先继续 `test_isolated_orphan_activity.py`：当前已证明在 `previous` 复现中 parent 发送 interrupt 时 PID/Popen/stdin/generation 并未发生可见替换，真正异常是 child 在读取第一条 `turn.start` 后第二次 stdin read 直接 EOF 并进入 `stdin_closed`，而 parent 仍看到同一 live Popen、poll=None、stdin open。直接 pytest 也复现，排除 per-file runner；独立 probe 无论 venv launcher 还是 `_base_executable` 都不会复现 EOF。下一步只做最小 handle/close ownership 取证，证明是谁关闭/失效了 child stdin，再做最小修复。之后验证 `test_serve_exit_flush.py` 的 Windows signal 分支，再处理 agent-core WinError 1314 symlink capability。Fast 绿不能冲掉 Deep 红，以 Backend Lab failure ledger 为最终证据。

## 20. Handoff status

This conversation intentionally stops here after writing the handoff.
No new production fix was committed in this conversation.
The main new value is the narrowed runtime evidence above: **the reproduced failure is currently a child-stdin EOF ownership problem, not a demonstrated HostSupervisor respawn/generation mismatch.**
