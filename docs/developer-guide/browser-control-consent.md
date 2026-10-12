# Browser-control consent policy (slice 1)

Status: a pure policy module with unit tests. It is not wired into any tool, the browser router, the Desktop UI or an approval store. Nothing here installs an extension, clicks a dialog or touches a real browser.

## Location and why

`tools/browser_control_consent.py`, next to the existing browser permission seam:

- `tools/browser_preview_approval.py` classifies browser risk for `unified_browser_tool._host_action`.
- `tools/browser_extension_router.py` routes page actions to the bound controller.
- `tools/computer_use/tool.py` gates desktop actions in `_reject_unsafe` and `_request_approval`.

The install and native-dialog steps come from the computer-control tool, and page actions come from the browser router. Both need the same answer, so the policy sits in `tools/` rather than inside one tool. It is not in `tools/approval.py`: that module is the interactive prompt transport, and a standing, revocable, per-connection owner grant is a different authority. It is not in `gateway/browser_control_broker.py`: the broker is transport-neutral, and the policy must not depend on a transport. The module imports the broker's capability allowlist lazily, because importing `gateway` loads its config, session and delivery modules.

## Rules

Each step is one request type. It is allowed only when every check for that type passes. Otherwise it is denied with a reason.

| Step | Allowed when | Denied, for example |
|---|---|---|
| Install the extension | Exact `@playwright/mcp` 0.0.83. Target is inside the isolated profile root. An owner record for this exact connection approves `install_extension` at that pin. | `@latest`, ranges, other versions. Targets outside the root, including `..` and sibling paths. No record, or a record from another session. |
| Start the Playwright child | Exact pin. Launched from the lockfile-installed CLI. Environment names are on the prototype's allowlist. Arguments are allowlisted flags with no credential markers. Owner approved `attach_controller`. | `npx` or a registry spec. `STARDUST_API_SERVER_KEY` or the grant in the environment. `Authorization: Bearer` in argv. Unlisted flags. |
| Attach the controller | The one-time pairing grant was consumed (exactly `True`). Owner approved `attach_controller`. | A grant that was not consumed (pairing bypass). No record. |
| Click a native approval window | Origin is `native_ui`. Attributed to the supervisor's `bridge_launch_record`. Owner approved `click_native` for this connection and this permission. | A dialog painted by a page, even with a matching record. Attribution by window title or page text. An unapproved permission. |
| Page action after connection | Channel is `controller`. Capability is in the broker allowlist. Controller is live. Owner approved `attach_controller`. | Native pixel input. Developer capabilities (`browser_cdp`, `browser_evaluate`). Artifact upload or download. Offline controller. |
| Copy cookies or profile data | Never. | Always. |

Across all steps:

- An owner record counts only when `source == "stardust_app"`, it is not revoked, it has not expired, and its identity matches the step's identity exactly. Identity is principal, profile, session, controller, browser profile and transport.
- Stop control: when `kill_switch_engaged` is set, every step is denied.
- Audit: `authorize()` writes the decision through a sink before the caller may act. If the write fails, the step is denied. The event carries no grant, token, cookie, page text or profile path.
- Page text and dialog wording are never inputs. A page cannot grant anything, even with text that asks for approval.

## Verified

- `tests/tools/test_browser_control_consent.py`: 91 cases pass under `scripts/run_tests.sh`. They cover every allowed and denied path in the table, the page-content approval cases, the stop control, the audit fail-closed path, and a change to each of the six identity fields.
- Mutation check: removing the origin guard, the record-source guard, the pairing guard, the argv credential guard or the isolated-root guard each fails its own tests. The module was restored byte-identical afterwards.
- The pin equals the version locked in `experiments/playwright-mcp-host-bridge/package-lock.json`. A test asserts this.
- `ruff check` passes on both new files. The neighbouring browser tests (`test_browser_extension_router.py`, `test_browser_preview_approval.py`) and the repo-wide source-scan guards still pass.

## Not verified and not built

- **Approval store and Desktop endpoint.** The policy trusts only the records passed in. The store must write owner records from the app's own approval action and nowhere else.
- **Wiring.** No tool, router or Desktop code calls the policy yet. The install and dialog actions do not exist in the tools. Wiring the approval requirement into `_host_action` now would deny every existing host action, because no records exist. The next slice wires it behind `browser.extension_control.enabled`.
- **Caller-asserted facts.** `origin`, `attribution`, `channel`, `controller_live` and `pairing_grant_consumed` come from the caller. Wiring must derive each one from the code path or the broker, never from request content.
- **Native versus page origin.** `native_ui` must come from OS window ownership, the browser's own top-level window. It must never come from screenshot pixels, because a page can paint a fake dialog inside its viewport. This is the hardest open risk, and this slice does not solve it.
- **Extension identity.** The policy pins the npm package, not the extension's store ID or signature. Checking the installed extension against its official listing is not done.
- **One-shot install.** The policy cannot track consumption, so the store should expire an install record after one use.
- **Live checks.** Real Chrome, a real extension install, real native dialogs, the Desktop stop control and the visible step UI were not exercised.
