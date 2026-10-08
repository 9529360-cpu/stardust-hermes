# Browser bridge validation checklist

- [x] Lock MCP and SDK dependencies; start the real pinned MCP and check all negotiated tools.
- [x] Run Electron bridge children in Node mode with bounded startup and shutdown.
- [x] Exchange a single-use, profile/session-bound launch grant through the actual local serve backend.
- [x] Reject remote peers, gated cloud servers, wrong scope, grant replay and unsupported protocol.
- [x] Resolve the Desktop conversation's tool identity without changing its durable conversation ID.
- [x] Enable control only on explicit Connect; preserve the default-off setting and explicit extension consent.
- [x] Prove official extension control of an existing isolated Windows Chromium tab, preserving its cookie.
- [x] Verify input, click, snapshot, scroll, result routing and fail-closed disconnect through the backend.
- [x] Verify native approval URL launch and selection in already-running isolated Windows Chromium.
- [x] Exercise Connect and Disconnect in the actual Desktop window with a real isolated serve backend.
- [ ] Validate installed Google Chrome profile discovery without the test-only isolated Chromium executable/profile override.
- [ ] Repeat the live browser/Electron/backend smoke on macOS and Linux.
- [ ] Validate the packaged production installer on each supported platform.

Remote backend browser pairing is intentionally unavailable in this local bridge. No automatic retries reuse a consumed Desktop grant.
