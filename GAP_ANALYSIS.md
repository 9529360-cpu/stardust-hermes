# Stardust gap analysis (vs. product-grade personal assistant)

Snapshot: `main` @ `cb71b79c67` (imported 2026-10-09). Reference: JARVIS-PROGRAM.md phase-1 acceptance,
README.zh-CN.md, STARDUST.md, AGENTS.md, SOUL.md, and a Muse / Grok Bot style capability list
(own computer, connectors, background long tasks, routines, memory, approvals before send/buy,
multi-agent delegation, chat-first UX).

## What already exists (strong base, verified by reading code)

| Capability | Where | Status |
|---|---|---|
| Chat streaming / interrupt | `agent/stream_*`, `tui_gateway` `session.interrupt`, `agent/turn_recovery.py` | present |
| Model routing, fallback, cooldown, credential pools | `agent/route_health.py`, `agent/credential_pool.py`, `agent/fallback_cooldown.py` | present |
| Provider readiness | `tui_gateway/methods_config.py` `setup.status` / `setup.runtime_check` | credential-presence only (no live call) |
| Memory | `tools/memory_tool.py` (+`memory_tool_store.py`): add/replace/remove, file-backed | agent-tool only; no user-facing list/delete RPC |
| Background delegation w/ restart recovery | `tools/async_delegation.py` (SQLite, `recover_abandoned_delegations`) | present |
| Subagents | `tools/delegate_tool*.py`, RPC `subagent.list/interrupt/steer`, `delegation.status/pause` | present |
| Routines / scheduled jobs | `cron/` (scheduler, delivery, incidents), `tools/cronjob_tools.py` | present |
| Approvals | `tools/approval*.py` (smart gate, floors, permanent allowlist, yolo, gateway wait), RPC `approval.*` | present, **no audit trail** |
| Connectors | `tools/connectors/`, Microsoft Graph, Feishu, Slack/Discord/Telegram gateway | present |
| Own computer | terminal/browser/computer-use tools, code kernel | present |
| Desktop | `apps/desktop` Electron (React) | present; workbench panels for Work/Approvals/Memory not unified |

## Gaps, ranked by impact (each is concrete and testable)

1. **Approval audit trail (phase-1 #5).** No persisted record of approval decisions exists
   (`grep audit tools/approval*.py` → nothing). Add an append-only, redacted JSONL ledger under
   `$HERMES_HOME/audit/approvals.jsonl` written from the central gate (`_run_approval_gate` /
   `request_tool_approval` outcomes: approved-once / session / permanent / denied / blocked / auto
   via yolo/smart), secrets redacted via `agent/redact.py`, plus a reader API and RPC
   `approval.audit` (limit, session filter). Tests: decisions produce entries; secrets never appear;
   file survives "restart" (re-import); bounded size/rotation.
2. **Live model health check (phase-1 #2).** `setup.runtime_check` only verifies a credential exists.
   Add `live: true` option that performs a minimal real request (models list or 1-token completion)
   with short timeout, returning `{ok, latency_ms, error_kind (auth|not_found|rate_limit|network|timeout|server), message}`
   with the key redacted from any error text. Tests against a local fake OpenAI-compatible HTTP server
   (200, 401, 404, timeout) and a no-leak assertion.
3. **User-facing memory management (phase-1 #3).** Memory is only mutable by the agent tool. Add RPCs
   `memory.list` (entries per target: memory/user, with stable ids/index), `memory.remember`, `memory.forget`
   using `MemoryStore`, persisted to disk and reflected after reload. Tests: remember → new store instance
   sees it; forget removes it; unknown entry → clean error; profile-scoped HERMES_HOME.
4. **Unified Work ledger (phase-1 #4).** Long tasks are split across async delegations, background
   processes (`process_registry`), subagents and cron runs with no single status/cancel surface.
   Add `work.list` (normalized `{id, kind, title, status, started_at, updated_at, result_ref}`) and
   `work.cancel` (async delegation / background process), surviving restart via the existing SQLite
   records. Tests: seeded durable delegation shows up after re-init; cancel transitions status.
5. **Scoped standing authorizations (phase-1 #5 nuance, Grok Bot "approve before send/buy").**
   JARVIS-PROGRAM requires *not* re-prompting when an authorization already covers the action, and
   re-prompting when scope/recipient/amount changes. Today there are only per-session pattern approvals,
   a permanent allowlist and yolo. Add explicit grants `{action_kind, target/recipient, max_amount, expires_at}`
   checked before prompting, persisted, revocable, and audited (depends on #1). Tests: in-scope → no prompt;
   changed recipient / higher amount / expired → prompt.
6. **Chat error-recovery contract (phase-1 #1).** Verify and pin with tests that a mid-stream provider
   failure yields a structured, user-visible, retryable error event (no secret leakage, partial text kept,
   session usable for next turn), and that `session.interrupt` stops a stream promptly. Add tests at the
   `tui_gateway` seam with a fake streaming provider; fix any gaps found.
   **Done (2026-10-10):** Added `redact_sensitive_text(force=True)` to all user-visible error exits
   (`_fail_inflight_turn`, `turn_error_text`, `_emit_terminal_turn_error`, `_complete_turn_payload`,
   `_recover_turn_exception`, `_summarize_api_error` fallback, `agent_init_failed_message`,
   `resume_failed_message`). 10 new contract tests in `tests/tui_gateway/test_chat_error_recovery_contract.py`
   pin mid-stream failure → structured `message.complete` (`status: "error"`, `error_surface`,
   `recoverable`, `partial`), no API key leakage (exception + returned-error paths), partial text retention,
   next-turn usability, and `session.interrupt` cancel-flag + agent-interrupt dispatch.
7. **Routine creation from chat (Muse/Grok "routines").** Ensure a natural-language routine
   ("every weekday 8:00 summarize my inbox") maps to a cron job with delivery target, can be listed/paused/
   deleted via RPC, and its runs appear in the Work ledger (#4). Mostly wiring + tests.
8. **Desktop workbench panels (phase-1 #6).** Wire Work, Approvals (incl. audit), and Memory panels in
   `apps/desktop` to the RPCs above, with vitest coverage. Ranked last for the autonomous loop because
   AGENTS.md requires real Electron-window inspection for UI acceptance, which the loop cannot do.

## PR333 reviewed implementation boundaries

- Approval auditing is best-effort and nonblocking: a bounded daemon queue pins each
  entry to its originating profile. Saturation, disk failure or process exit can lose
  telemetry; audit persistence is not an approval/interrupt delivery prerequisite.
- `live: true` checks `/models` HTTP reachability only (`probe_kind: endpoint_reachability`,
  `inference_ok: null`). A 2xx is **not** evidence that the selected model can infer.
  No implicit completion request or paid inference fallback is made on 404.
- `memory.forget` uses exact unique text, or index **plus expected_text** from the
  displayed entry. Both are checked under the existing file lock; stale selections
  cause zero writes. Deletion reuses the byte-preserving exact-entry path rather than
  rewriting/deduplicating all entries. Index alone is deliberately rejected.
- The Work RPC exposes/cancels only live subagents with exact session/transport/
  generation authority. Profile-wide process and persisted async-delegation records
  lack that proof and are not exposed or cancellable through this RPC. The internal
  profile ledger remains available, with process cancellation preserving output delivery.
- Grant CRUD remains management-only: records do **not** automatically approve
  commands or sends. Only non-monetary command/send records can be added; purchase,
  payment and amount-bearing grants are unsupported. No payment capability is claimed.
  MCP send approvals are once-only: alias/home-channel changes cannot inherit an old
  session/permanent send approval. Safe resolved-recipient automatic grants remain deferred.

## Notes
- Items 1–4 are backend-only and verifiable with `scripts/run_tests.sh`; they are the loop's first targets.
- Every item must add focused regression tests and keep existing related suites green.
