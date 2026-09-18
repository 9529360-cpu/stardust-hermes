# Frontend Integration Contract Handoff — 2026-09-18

## 1. Scope and authority

This handoff is for the Desktop/frontend integration boundary against the current Gateway contract.
It does not authorize Desktop UI changes from the backend worktree.

The backend is the authority for session, turn, tool, Todo, Goal/Loop, interruption, recovery,
and terminal state. The renderer may cache and project backend state, but must not manufacture
"running", "stopped", "completed", "failed", or "waiting" without backend evidence.

Wire authority:
- Python Pydantic contract owners: `tui_gateway/contracts/**`.
- Generated TypeScript: `apps/shared/src/gateway-contract.generated.ts`.
- Generated OpenRPC: `apps/shared/src/gateway-contract.openrpc.json`.
- Event envelope/client synthetic additions: `apps/shared/src/gateway-events.ts`.
- Do not hand-edit generated contract artifacts; run `scripts/gen_gateway_contracts.py`.

Internal compute-host frames such as `turn.start`, `turn.started`, `turn.end`, `turn.error`
and `interrupt.ack` are runtime implementation protocol, not Desktop-facing lifecycle events.

## 2. Session identity and live state

Primary methods:
| Method | Frontend use | Authoritative result |
| --- | --- | --- |
| `session.create` | Mint a live runtime session | runtime `session_id`, `stored_session_id`, messages, `SessionLiveInfo` |
| `session.resume` | Attach stored session and rebuild runtime state | messages + live/recovery snapshot |
| `session.activate` | Attach an already-live session without closing another | same recovery/live snapshot shape as resume |
| `session.active_list` | Backend-owned overview of live sessions | `SessionActiveItem[]` |
| `session.list` | Stored human-facing session browser | persisted session rows |
| `session.history` | Durable display transcript | projected `TranscriptMessage[]` |
| `session.events.since` | Reconnect event replay | events, `latest_seq`, `truncated`, `epoch`, open requests |
| `session.close` | Explicit live-session teardown | `{closed: boolean}` or RPC error |

`SessionLiveInfo` is the current-session settings/state snapshot. Useful fields include:
`cwd`, `branch`, `project`, `running`, `turn_started_at`, `title`,
`stored_session_id`, model/provider/reasoning settings, usage, profile, tools/skills, and `lazy`.

`session.active_list` provides backend-projected `status`:
`idle | starting | waiting | working | streaming | resuming`.
For session-list chrome, use this value directly rather than inferring status from local timers.

Resume/activate additionally expose the reconnect snapshot:
- `running`, `turn_started_at`, `status`;
- `inflight`: current or retained failed turn bubble;
- `queued`: queued user prompt;
- `pending_approval`, `open_requests`, `pending_connection`;
- `todo_state`;
- `auto_continue`: crash-interrupted turn scheduled for backend continuation.

## 3. Prompt and turn lifecycle

`prompt.submit` returns `status: streaming | queued | steered | redirected`
(and `turn_isolation` when dispatched through compute-host isolation).
Desktop-facing turn events:
| Event | Meaning |
| --- | --- |
| `message.start` | A turn began streaming |
| `message.delta` | Assistant output chunk |
| `message.interim` | Sealed assistant commentary beside tool calls |
| `message.complete` | Terminal turn outcome |
| `session.info` | Refreshed live session snapshot |
| `session.resume_progress` | Deferred history hydration phase |
| `session.usage` | Mid-turn usage tick; final usage belongs to `message.complete` |
| `status.update` | Transient human-facing status line, not a terminal-state authority |

`message.complete.status` is the frontend terminal outcome enum:
`complete | error | interrupted`.
It may also carry `failure_reason`, `error`, `recoverable`, `error_surface`,
`partial`, final usage, text, and reasoning.

Rules:
- Do not declare a turn complete because streaming stopped locally.
- Do not declare a turn complete because the last tool completed.
- Do not treat `status.update` text as a state machine.
- On reconnect, rebuild from resume/activate snapshot first, then replay events.
- If `session.events.since.truncated === true`, refetch authoritative snapshots instead of filling gaps by inference.

`session.close` now has stronger lifecycle semantics for isolated turns:
`closed: true` is returned only after compute-host cancellation was positively acknowledged
and the parent mirror observed terminal settlement (`running=false`) before teardown.
A missing live session returns `closed: false`; failure to settle returns RPC error 5019.
## 4. Interrupt / Stop semantics

Method: `session.interrupt`.

Result:
- `status: interrupted | not_interrupted`;
- optional `interrupted`;
- optional `turn_isolation`.

For an isolated compute-host turn, an `interrupted` RPC result is returned only after the child
returned a real `interrupt.ack(applied=true)`. This proves cancellation was applied; it does not
by itself prove that the entire turn stack has unwound.

Recommended Desktop projection:
1. User clicks Stop -> local intent may show "Stopping…".
2. `session.interrupt` success -> cancellation is confirmed/applied.
3. Keep the turn non-terminal until `message.complete.status === "interrupted"` or another
   backend snapshot provides terminal evidence (`running=false`).
4. Never turn a local button click into "Stopped" without that backend evidence.

A confirmed interrupt also retires the crash-recovery marker so a later resume does not resurrect
the turn the user explicitly stopped.

## 5. Tool lifecycle

Authoritative events:
- `tool.start {tool_id, name, args/context/...}`;
- `tool.generating {name}` is argument-generation progress only;
- `tool.complete {tool_id, name, duration_s, result/summary/diff/...}`;
- `tool.output_risk` is an advisory safety/result classification event.

Correlate `tool.start` and `tool.complete` by `tool_id`. A tool completion is terminal for that
tool call only; it is not terminal evidence for the agent turn.
There is currently no separate durable "current in-flight tool" snapshot in `SessionLiveInfo`.
Normally reconnect reconstructs tool chrome through `session.events.since`. If replay is truncated,
clear uncertain in-flight tool UI and wait for/refetch backend evidence rather than guessing.

## 6. Todo/task state

Authoritative snapshots:
- resume/activate: `todo_state {todos, revision}`;
- live update: `todo.updated {todos, revision}`;
- Todo tool `tool.complete` can also carry `todos` + `revision`.

Treat the full Todo snapshot as replace-state, not as a frontend-owned mutation log.
Use `revision` as the stale-update fence: an older revision must not overwrite a newer snapshot.
Do not use `tool.start.args` as Todo truth; those args may be a partial merge and execute before
the authoritative result exists.

The individual Todo item schema remains producer-owned/open on the shared wire. Desktop should
render known fields defensively and preserve the backend snapshot as authority.

## 7. Goal / Loop / Heartbeat state

Methods:
- `session.control.read({session_id})` -> exact `SessionControlSnapshot`;
- `session.control(...)` -> resulting snapshot plus dispatch envelope.

Event:
- `session.control.update {control}`.

`SessionControlSnapshot` contains `goal`, `loop`, `heartbeat`, `revision`, `updated_at`.
The revision hashes the visible control state; frontend cache invalidation should use it rather than
attempting to reproduce Goal/Loop transitions locally.
Goal fields exposed to Desktop include `title`, backend-owned `status`, turns/max-turns,
contract, subgoals, gates, pause/verdict/reason fields, and optional `wait_barrier`.
Loop exposes backend-owned `status`, cadence/tick counters, `awaiting_response`,
`deferred_by_goal`, pause and stop reasons. Heartbeat exposes backend-owned status/counters.

Do not invent Goal/Loop completion or waiting states from timers. Use the snapshots/update events.

## 8. Waiting, failure, crash and recovery projection

Backend evidence for "waiting" includes:
- `session.active_list[].status === "waiting"`;
- open server requests / `pending_approval`;
- `pending_connection`;
- Goal `wait_barrier` or Loop `awaiting_response` where applicable.

Backend evidence for failure/recovery includes:
- terminal `message.complete.status === "error"`;
- `inflight.error/status/recoverable/error_surface` on reconnect;
- `auto_continue {attempt, interrupted_at}` when crash recovery was scheduled;
- `session.reclaimed` when backend automatic lifecycle ownership removes a live session.

Internal crash markers, compute-host PID/boot generation and raw compute-host terminal frames are
not frontend contracts. The renderer must not infer crash/recovery from process observations.

`request.cancel` means the backend withdrew a specific open server request; clear only that card.
`session.reclaimed` is backend lifecycle authority and should remove/refresh the affected live view.
## 9. Hosted-room interrupt generation compatibility

`SessionInterruptParams` now has one additive optional field:

```ts
{
  session_id: string
  profile?: string | null
  expected_hosted_task_id?: string | null
  expected_hosted_execution_generation?: number | null
}
```

Semantics:
- `expected_hosted_execution_generation` is meaningful only with `expected_hosted_task_id`.
- When task id and/or supplied generation no longer match the running hosted task, backend returns
  `{status: "not_interrupted", interrupted: false}` and does not cancel the newer execution.
- The generation is an anti-stale retry fence. It is not a frontend sequence to increment.
- New hosted-room callers should echo the exact backend-owned execution generation associated with
  the task they intend to cancel. Hosted-room wire shapes already expose this identity, including
  `RoomTaskReceipt.execution_generation`; approval requests likewise carry `execution_generation`.
- Ordinary chat/session Stop should omit both hosted-task fence fields.

Compatibility:
- Old clients that omit `expected_hosted_execution_generation` keep the prior task-id-only behavior.
- The field is optional and additive; no old request shape is invalidated.
- Generated TypeScript and OpenRPC now both include the field.
- Frontend must never guess a generation from attempt count, timestamps, event sequence, or local retries.

## 10. Recommended reconnect/integration sequence
1. Establish the Gateway connection and consume `gateway.ready`.
2. `session.resume` or `session.activate`.
3. Replace local transcript/session projection with the returned messages + `info`.
4. Restore `inflight`, queued prompt, approvals/open requests, pending connection, Todo and recovery state.
5. Read `session.control.read` when Goal/Loop/Heartbeat chrome is shown.
6. Replay `session.events.since(last_seen)`.
7. If replay reports `truncated`, discard uncertain event-derived chrome and refetch snapshots.
8. Continue applying live events in Gateway sequence order.
9. Use `message.complete` / backend snapshots for terminal turn state.
10. Use `todo.updated.revision` and control `revision` to reject stale cached projections.

## 11. What Desktop must not infer

Do not infer:
- running from "we sent prompt.submit";
- stopped from "the Stop button was clicked";
- completed from "no delta arrived recently";
- completed from the last tool.complete;
- waiting from a local timeout;
- Todo completion from tool arguments;
- Goal/Loop status by reproducing backend timers;
- crash/recovery from WebSocket loss;
- compute-host generation from process/PID observations.

A renderer may optimistically show an intent state such as "Sending…" or "Stopping…", but it must
transition to semantic runtime state only when the backend provides evidence.

## 12. Known contract gaps / deliberate non-contracts
1. There is no unified `TaskState` object spanning Session + Goal + Todo + Tool. This is deliberate
   for now: those existing owners remain authoritative and should be projected, not duplicated.
2. There is no frontend-visible compute-host health/PID/boot-generation contract. Runtime isolation
   is an implementation detail; use session/turn evidence for product state.
3. There is no durable in-flight tool snapshot outside event replay. After a truncated replay,
   Desktop cannot authoritatively name the currently-running tool and should clear/neutralize that
   detail until new backend evidence arrives.
4. `status.update` text is intentionally transient and should not become a frontend state enum.
5. Todo item payloads are currently open/producer-owned; only the full snapshot + revision contract
   is stable at the shared boundary.

None of these gaps requires a new API for the first formal Desktop integration. If product UX later
requires durable current-tool recovery or runtime diagnostics, add the smallest backend-owned
snapshot only after proving that existing replay/snapshot contracts cannot satisfy the UX.

## 13. Integration summary

For the Context Rail, the safest backend-owned projection is:
- Session: `session.info` / resume snapshot / `session.active_list`.
- Turn: `message.start` -> streaming events -> terminal `message.complete`.
- Stop: `session.interrupt` confirms cancellation application; terminal evidence confirms stopped.
- Tool: `tool.start` -> `tool.complete` by `tool_id`.
- Todo: resume `todo_state` + `todo.updated`, guarded by revision.
- Goal/Loop: `session.control.read` + `session.control.update`.
- Recovery: `inflight`, `auto_continue`, terminal error, `session.reclaimed`.
- Reconnect: snapshot first, replay second; on truncation refetch, never guess.
