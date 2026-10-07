# Browser `browser_exec` compatibility evidence

This slice records migration capability without advertising new tools or executing
host Python. Evidence is local to the checked-in implementations at the source
revision; adapter names below are documented helper/CDP idioms, not claims that
`browser_exec` now implements legacy API semantics automatically.

| Legacy capability | Existing source | `browser_exec` evidence / adapter | State |
|---|---|---|---|
| Status | `tools/browser_tool.py`: `browser_status` serializes `tools/browser_tool_manifest.py` | `page_info()` gives current page summary; migration status still needs stable backend/session manifest integration | Adapter only; parity missing |
| Console and JS errors | `browser_console` returns console messages and uncaught JS errors from browser session | `cdp('Runtime.enable')` is a protocol primitive, not yet a complete buffered console/error reader | Missing; do not claim parity |
| Vision annotation | `browser_vision(annotate=True)` captures screenshot and returns annotation metadata | `capture_screenshot()` plus CDP/DOM inspection can aid analysis, but there is no direct equivalent to persistent annotation rendering | Partial; preserve legacy capability until parity |
| Structured actions | `browser_interact` exposes named interaction actions and results | `js`, `cdp`, `click_at_xy`, `fill_input` form action-specific primitives; `tools/browser_use_compat.py` makes the mapping explicit and rejects unknown actions | Mapping slice implemented; execution adapter remains future work |

## Scope and safety

The compatibility module is data-only. It neither enables host Python nor invokes
browser processes. Tests assert that action arguments survive mapping, unsupported
actions fail loudly, and the matrix does not overstate parity. Do not use skipped
tests to represent unavailable capability; add deterministic unit tests at the
adapter boundary instead.
