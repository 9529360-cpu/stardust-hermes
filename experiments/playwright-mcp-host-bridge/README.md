# Stardust Playwright MCP host-browser bridge (experimental)

**Status: testable prototype only. Not wired into production Stardust Desktop, not installed, not paired with a real Chrome.**

This folder preserves the independently developed adapter prototype in the Stardust repository for review. It is intentionally outside `tools/`, `plugins/`, `apps/desktop/` and npm workspaces: cloning/building Stardust does **not** launch it, grant a permission, or import a user's Chrome profile.

## Goal and existing contracts

- The already-merged unified `browser` tool has two targets: `in_app` operates the actual right-side WebView, and `host` must use an authenticated controller for the exact session.
- `gateway/browser_control_broker.py` owns the controller protocol. The API registration endpoint is `POST /v1/browser-control/register`; WebSocket endpoint is `/v1/browser-control/ws`; one-time tickets travel in WebSocket subprotocols.
- This experimental adapter translates approved broker commands into Microsoft's Playwright MCP `--extension` tools for existing user-approved Chrome tabs. It is **not** a new browser engine.
- A controller is not authoritative merely because it supplied a session-id string. The actual API principal/transport must match the caller. This binding is not implemented for Stardust Desktop yet.

## What's included

- `src/protocol.mjs`: loopback-only registration, negotiated capabilities, action mapping, bounded text output.
- `src/broker-client.mjs`: ticket-based WebSocket client, bounded retries, serialized action queue, cancel/duplicate suppression, no replay of actions on reconnect.
- `src/mcp-driver.mjs`: thin MCP client that checks the active server tool schemas, without passing the Stardust API token to the Playwright child.
- `src/main.mjs`: manual-only experimental process entry; does not run automatically.
- `test/*.test.mjs`: deterministic contract tests without live browser or gateway.

## Local unit tests

From this directory on Node 22 or later:

    node --test test/*.test.mjs

These tests use fake WebSocket/gateway/Playwright responses. They do **not** verify live Chrome/Edge, Windows session ownership, MFA safety, installation or native UI pairing.

## Installation is deliberately NOT turnkey

Before any real-world use: audit and pin a compatible released version of `@playwright/mcp`, validate its actual extension command flags and MCP schemas, install the SDK plus pinned package with a lockfile, and obtain the official extension through its trusted installation channel. Never use `@latest` or send the Stardust bearer token to Playwright.

The experimental entry currently expects four externally supplied values: `STARDUST_SESSION_ID`, `STARDUST_API_SERVER_KEY`, `STARDUST_CHROME_PROFILE_DIR` and `PLAYWRIGHT_MCP_PACKAGE` (must have exact semver). `STARDUST_GATEWAY_URL` defaults to `http://127.0.0.1:8642/`. Do not store credentials in shell history, tracked files or extension storage. A correct valid API session and explicit user approval are required.

**WARNING:** The prototype has no Desktop pairing UI, durable consent registry, kill-switch integration, managed process owner, real session/principal binding proof, browser extension version conformance or native cross-platform E2E tests. Do not deploy as a trusted controller until all are verified. It is intentionally disconnected from Stardust's active `host` routing.

## Safety / rollback

- Loopback only; requests to nonlocal gateway endpoints are rejected.
- No automatic profile-copy or browser-tab creation. Host mode never silently falls back to the right-side WebView.
- No raw CDP/eval, uploads or downloads. Credentials, one-time codes and card numbers must not go through model tool arguments.
- Rejected actions, expired tickets, missing controller, disconnect and cancellation must fail closed.
- To undo this prototype, drop this isolated folder; no runtime config, user state or persisted credentials are changed.

References: https://github.com/microsoft/playwright-mcp and https://playwright.dev/mcp/configuration/browser-extension

See `INTEGRATION_CHECKLIST.md` for the requirements before promotion to a shipped feature.
