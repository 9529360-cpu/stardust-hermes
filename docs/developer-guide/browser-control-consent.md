# Browser-control consent policy (slice 1)

Status: a pure policy module with unit tests, plus a Windows verifier (`tools/browser_window_origin.py`) that decides the `origin` of a native dialog from OS window data. Neither is wired into any tool, the browser router, the Desktop UI or an approval store. Nothing here installs an extension, clicks a dialog or touches a real browser. The probe below ran a throwaway browser and clicked nothing.

## Location and why

`tools/browser_control_consent.py`, next to the existing browser permission seam:

- `tools/browser_preview_approval.py` classifies browser risk for `unified_browser_tool._host_action`.
- `tools/browser_extension_router.py` routes page actions to the bound controller.
- `tools/computer_use/tool.py` gates desktop actions in `_reject_unsafe` and `_request_approval`.
- `tools/browser_window_origin.py` (Windows only) decides the `origin` of a native dialog from OS window data, which `NativeDialogClick` trusts.

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
- `tests/tools/test_browser_window_origin.py`: 48 cases pass under `scripts/run_tests.sh`. 42 use fake window records, and 6 are `windows_only` and create real windows in-process.
- Mutation check: removing the renderer-descendant rule fails 5 cases. Removing the instance binding fails 3. Removing each click-time rule (the verdict, hwnd, pid or renderer check) fails its own cases. The module was restored byte-identical each time.
- `ruff check` passes on the changed files. The repo does not enforce `ruff format`, and untouched modules fail it too.
- The packaging, source-scan and neighbouring browser tests pass: 243 tests across 9 files.

## Window origin: verifier and probe (Windows)

`tools/browser_window_origin.py` gives the `origin` for `NativeDialogClick` from OS window data. A window is `native_ui` only when all of these hold:

- it is visible, and it is a top-level window or a window owned by one;
- its process image is the pinned executable the caller passes in, compared the way Windows compares paths, and its process belongs to the launched browser instance;
- its class is `Chrome_WidgetWin_1`, and its owner, if it has one, is a top-level Chromium window of that instance;
- no `Chrome_RenderWidgetHostHWND` is its ancestor or a descendant.

A window that is, or sits inside, a renderer surface, or that hosts one, is `web_page`. Anything else is `unknown`, and the policy denies both.

Three checks go beyond the brief. Visibility is required because a dialog must be on screen to be clicked. The renderer-descendant rule is needed because the browser's own frame and any page-created popup otherwise pass as top-level Chromium windows. The instance binding is needed because a second Chrome on the same machine has the same executable. The pure function takes snapshots, so the fake-record tests run on any host. The policy seam test shows that only a `native_ui` verdict lets the policy click.

Probe, read-only, 2026-10-12, Windows 11. Chrome 154.0.8037.98 and Edge 155.0.4283.45, one fresh temporary profile per run. A 127.0.0.1 page requested geolocation. Nothing was clicked and no keystroke was sent.

| Window | Chrome | Edge |
|---|---|---|
| Location permission prompt | `native_ui`. Top-level `Chrome_WidgetWin_1`, owned by the frame, no renderer. First seen 0.16 s after the request. A capture shows the prompt. | `web_page`. Same class and owner, but it hosts a `Chrome_RenderWidgetHostHWND`, so Edge draws this prompt as web content and the verifier denies it. The literal rules would say native. |
| HTML fake dialog, hit test at its centre | `web_page` (the page renderer) | `web_page` |
| Browser frame | `web_page`. The four literal rules would say native. | `web_page`. The literal rules say native. |
| Edge translate prompt | not sampled | `native_ui` |

Four earlier Chrome runs released the request at once, without activating the window, and none showed a prompt window. The run that released the request only after activating the window showed one. The cause is not established. Edge's translate prompt is `native_ui`, so `native_ui` means browser UI, not a permission prompt. The policy's permission and attribution checks still decide what may be clicked. One run found 22 windows of the same executable belonging to other processes, which is why the instance binding is required.

## Click-time recheck

Just before a click, `decide_click_at_point` looks up the window at the target point with `window_at_point`, takes a fresh snapshot and calls `decide_click`. The click may proceed only when the window there is the one that passed `native_ui`, with the same hwnd, pid and class, and no renderer surface is that window, one of its ancestors or one of its descendants. A denial is logged with its reason, and the caller must not click. The wrapper never clicks.

The pure function is tested with fake records. The same window passes. A different hwnd, a different pid, a different class, a page-drawn window at the point and no window at the point are each denied. A window that did not pass `native_ui` is never clickable, even if it is the one at the point. Two real-window cases cover the wrapper. No click path calls it yet.

The rule is strict on purpose. A point covered by a child HWND of the approved window returns a different hwnd, so the click is denied. That fails closed, but a caller must aim at the window's own surface. The live check below did not exercise this case. Live check, no clicks, Chrome 154: at the prompt's centre the recheck allowed the click, because the hit was the prompt itself. At the fake dialog's centre it denied the click as page content, and the denial was logged.

## Not verified and not built

- **Approval store and Desktop endpoint.** The policy trusts only the records passed in. The store must write owner records from the app's own approval action and nowhere else.
- **Wiring.** No tool, router or Desktop code calls the policy yet. The install and dialog actions do not exist in the tools. Wiring the approval requirement into `_host_action` now would deny every existing host action, because no records exist. The next slice wires it behind `browser.extension_control.enabled`.
- **Caller-asserted facts.** `origin`, `attribution`, `channel`, `controller_live` and `pairing_grant_consumed` come from the caller. Wiring must derive each one from the code path or the broker, never from request content.
- **Native versus page origin.** The verifier decides `origin` from OS window data, not pixels, and the probe above confirms it for Chrome 154 and Edge 155. Still open: Edge draws its permission prompt as web content, so it is denied (fail closed), and certifying it needs a signal beyond window ownership that tells browser UI from page content. `decide_click_at_point` does the click-time recheck, but no click path calls it yet. Other OS dialogs, such as file pickers, are `unknown` and were not probed.
- **Extension identity.** The policy pins the npm package, not the extension's store ID or signature. Checking the installed extension against its official listing is not done.
- **One-shot install.** The policy cannot track consumption, so the store should expire an install record after one use.
- **Live checks.** The probe observed real Chrome and Edge permission prompts, but only to classify them. A real extension install, a real click, the Desktop stop control and the visible step UI were not exercised.
