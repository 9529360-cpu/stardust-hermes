# Browser bridge integration checklist

Prototype base: Stardust `main` after merged PR #319. Always check current `origin/main` before promotion.

- [x] Isolate experimental adapter and deterministic unit tests, without enabling any runtime/extension permissions.
- [x] Read the real Browser Control Broker v1 ticket, command/cancel/result contract.
- [ ] Confirm official Playwright MCP `--extension` invocation and CLI flags for a pinned, audited release (not demonstrated yet).
- [ ] Lock complete Node dependency graph and review licenses/security advisories.
- [ ] Attach via trusted local process owner with bounded startup, restart, shutdown, kill-switch, and secret redaction.
- [ ] Bind an actual Desktop-originating conversation to its **server-derived** principal, session and transport; user-supplied IDs are not sufficient.
- [ ] Expose explicit browser/profile/tab selection, site-level approval, disconnect, and revoke in Desktop UI.
- [ ] Prove live extension handshakes only grant the intended Chrome tab and never leak user cookies or authorization to an unintended session.
- [ ] Add real endpoint contract tests for registration, typed controller frames and error responses, plus wrong identity/session/transport, disconnect/reconnect, replay, duplicate commands, timeouts and cancellations.
- [ ] Run real Chrome/Edge + Electron + gateway smoke on supported Windows/macOS/Linux targets; confirm same tab, cookies, clicking, typing, scrolling, hide viewer, and recovery.
- [ ] Reconcile CI and exact remote HEAD, then review and merge. Keep native host mode disabled until all safety/UX gates are met.

Local developer machine was offline during this prototype preservation; no claim is made about syncing `D:\项目\Stardust-Jarvis` or running tests on that host.
