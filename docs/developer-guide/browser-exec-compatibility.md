# Browser `browser_exec` compatibility evidence

This slice records migration capability and the boundaries still open for later
slices. The structured snapshot/reference contract is implemented through the
`browser_exec` CLI path; the other adapter names below are documented helper/CDP
idioms, not claims of automatic parity with every legacy API.

| Legacy capability | Existing source | `browser_exec` evidence / adapter | State |
|---|---|---|---|
| Status | `tools/browser_tool.py`: `browser_status` serializes `tools/browser_tool_manifest.py` | `page_info()` gives current page summary; migration status still needs stable backend/session manifest integration | Adapter only; parity missing |
| Console and JS errors | `browser_console` returns console messages and uncaught JS errors from browser session | `cdp('Runtime.enable')` is a protocol primitive, not yet a complete buffered console/error reader | Missing; do not claim parity |
| Vision annotation | `browser_vision(annotate=True)` captures screenshot and returns annotation metadata | `capture_screenshot()` plus CDP/DOM inspection can aid analysis, but there is no direct equivalent to persistent annotation rendering | Partial; preserve legacy capability until parity |
| Structured snapshot/reference actions | `browser_snapshot` returns interactive refs for the legacy browser tools | `browser_snapshot_refs(max_items)` returns opaque `@ax1` refs; `browser_click_ref` and `browser_fill_ref` revalidate the current frame, loader, node role/name, visibility, and editability before acting | Native contract implemented; refs are navigation-scoped and require CDP Accessibility/DOM/Runtime support |
| Structured actions | `browser_interact` exposes named interaction actions and results | `browser_click_ref`/`browser_fill_ref` cover validated click/fill; `js`, `cdp`, `click_at_xy`, `fill_input` remain action-specific primitives for other operations; `tools/browser_use_compat.py` rejects unknown actions | Partial native adapter; other interaction parity remains future work |
| Execution errors | Built-in browser tools return JSON error payloads | `browser_exec` now returns `success: false`, `error`, and a stable `error_type` for validation, routing, launch, timeout, and process failures | Structured envelope implemented; browser capability unchanged |

## Scope and safety

The structured snapshot helpers are injected into the Browser Use CLI program and
use only its pre-imported raw-CDP helper; they do not import or enable host Python.
Tests execute the helper preamble against a fake CDP boundary, proving structured
refs, live-node validation, stale-ref rejection, and secret-safe fill results. The
compatibility matrix remains evidence-backed: console buffering and persistent
vision annotations are still not claimed as migrated parity. Do not use skipped
tests to represent unavailable capability; add deterministic unit tests at the
adapter boundary instead.
