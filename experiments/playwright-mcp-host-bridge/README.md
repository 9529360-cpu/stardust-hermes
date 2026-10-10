# Stardust Playwright MCP host-browser bridge

The Desktop browser settings explicitly pair an existing Chrome profile with the current conversation. Electron owns the bridge process; the local `hermes serve` backend supplies a short-lived, single-use launch grant. The bridge exchanges it at `/v1/browser-control/register` for a WebSocket ticket, carried only in the `/v1/browser-control/ws` subprotocol. Profile, conversation and controller identity come from the server-held grant.

Install the [official Playwright MCP Browser Extension](https://playwright.dev/mcp/configuration/browser-extension) in Chrome. Open the intended tab, choose the profile directory in Stardust's browser settings, and select Connect. On the first browser action, the official extension asks which existing tab may be controlled. Approve that tab explicitly. Disconnect stops the bridge and revokes the conversation's authorization. A lost connection requires explicit pairing again.

This path supports a local Desktop backend. Remote/cloud backends are refused. Building or opening Stardust does not pair Chrome automatically. The `browser.extension_control.enabled` setting defaults off; clicking Connect enables it for the chosen Stardust profile. Chrome cookies stay in Chrome; no profile snapshot or approval-bypass token is passed to the bridge.

The dependency graph pins `@playwright/mcp@0.0.83`. Its exported package manifest resolves the actual CLI entry. Electron children run in Node mode with a minimal environment; Playwright receives neither the Stardust launch grant nor API credentials. The CLI uses `--extension --caps vision` so the negotiated scrolling operation exists. Browser actions are serialized, bounded and deduplicated; canceled or stale queued commands do not run after disconnect.

Supported operations: navigate, snapshot, click, type, scroll, keyboard press, back and tab listing. Raw evaluation/CDP, uploads and downloads are outside this adapter's negotiated capabilities. A paired browser failure never silently selects another browser.

## Validation

Run `npm ci` and `npm test` in this directory. Tests include the actual locked MCP subprocess and the real process entry against an isolated registration/WebSocket fixture, without opening personal Chrome. Python endpoint/session tests live under `tests/hermes_cli/test_browser_control_routes.py` and `tests/gateway/test_browser_control_desktop.py`; Electron supervisor and settings tests live in `apps/desktop`.

Isolated Windows smoke tests exercised Electron Node mode, the actual serve backend, session-context tool routing, the official extension, and an existing temporary Chromium tab: input, click, snapshot, scroll, preserved cookie and disconnect all passed. A headed run also verified that MCP automatically opened the official approval page in the already-running isolated browser. The actual Desktop window connected and disconnected through its production UI and backend. Browser profiles and approval artifacts are external temporary test data, never repository content. Installed Google Chrome profile discovery, packaged installers and macOS/Linux end-to-end behavior still require platform validation.
