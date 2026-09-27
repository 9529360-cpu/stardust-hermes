# Desktop Install Identity Migration Plan (Hermes → Stardust)

Status: **plan only** — appId, executable name, and protocol scheme are
**not** changed by this document. This is the migration-sensitive piece
flagged by root `AGENTS.md` ("Hermes naming may remain at compatibility
boundaries... A compatibility rename requires a migration plan, fallback/
rollback behavior, existing-install coverage, and data-preservation proof")
and tracked in `9529360-cpu/stardust-hermes#10`.

## 1. Full inventory

### 1a. Installer/OS-level identity (`apps/desktop/package.json` → `build`)

| Field | Current value | OS-level effect |
|---|---|---|
| `name` | `hermes` | npm package name only; not OS-visible |
| `productName` | `Hermes` | Windows/Linux install dir, Start Menu name, default `app.getName()` |
| `appId` | `com.nousresearch.hermes` | macOS `CFBundleIdentifier`, Windows AUMID base, NSIS registry uninstall key, Squirrel/MSI product code seed |
| `executableName` | `Hermes` | Binary filename on disk (`Hermes.exe`, `Hermes` on Linux/macOS) |
| `protocols[0].schemes` | `hermes` | `hermes://` OS protocol-handler registration |
| `artifactName` | `Hermes-${version}-${os}-${arch}.${ext}` | Installer/artifact filenames (also hardcoded again in `scripts/test-desktop.mjs:127,144`) |
| `mac.extendInfo.CFBundleExecutable` / `CFBundleName` | `Hermes` | macOS `.app` bundle internal name/binary (display name is already `Stardust` via `CFBundleDisplayName`) |
| `nsis.shortcutName` / `uninstallDisplayName` | `Hermes` | Windows Start Menu shortcut label, "Add/Remove Programs" entry name |

No custom NSIS `.nsh` script, Linux `.desktop` file, or `.plist` file exists
in-repo — all three are generated entirely by electron-builder from the
table above at build time, so they inherit whatever these fields say with
no separate edit surface.

### 1b. Runtime code tied to the same identity

- `apps/desktop/electron/main.ts` keeps `APP_NAME = process.env.HERMES_DESKTOP_APP_NAME || 'Hermes'`, then calls `app.setName(APP_NAME)`. That default is load-bearing because Electron derives the default `userData` location from the application name; desktop-local connection/config state lives under that directory.
- The Windows AUMID is still set explicitly to `com.nousresearch.hermes` and must stay aligned with `apps/desktop/package.json` `build.appId` until a coordinated migration exists.
- Deep links are still a compatibility surface. Packaged builds use `HERMES_PROTOCOL = 'hermes'`; dev builds use `hermes-dev`. `DEEPLINK_SCHEMES` accepts both `hermes-dev` and `hermes` in dev, and only `hermes` when packaged, while OS registration still registers the current primary `HERMES_PROTOCOL`. A future Stardust scheme therefore needs additive dual-registration and parsing, not a string replacement.
- `apps/desktop/electron/desktop-uninstall.ts` still recognizes an install directory named `Hermes` and the inherited Linux binary naming. That detection must keep recognizing old installs for any future visible install-name migration.
- `hermes_cli/doctor_platform.py`, `hermes_cli/main_desktop.py`, `hermes_cli/update_cmd_maint.py`, and the desktop user guide still use `com.nousresearch.hermes` for macOS TCC resets. They must move only with the real bundle id.
- `hermes_cli/main_desktop.py` / `hermes_cli/subcommands/gui.py` still expose the self-signed identity name `Hermes Local Signing`; this is operational compatibility, not product copy to rename casually.
- `hermes_cli/managed_uv.py` uses the separate identifier `com.nousresearch.hermes.managed-python` for managed Python. It is independent from the Electron bundle id and has its own migration cost.

### 1c. Update mechanism (not electron-builder autoUpdate)

There is **no** `publish`/`autoUpdater`/`latest.yml` feed in this app —
`package.json build` has no `publish` key and `electron-updater` isn't a
dependency. Updates are a **git-based** self-update:
`apps/desktop/electron/bootstrap-runner.ts:48` (`STARDUST_SOURCE_REPO =
'9529360-cpu/stardust-hermes'`) and `electron/update-remote.ts:16-17`
already point at the Stardust-owned repo. `electron/updater-process.ts`
hands off to `scripts/desktop-update/windows.ps1` / `posix.sh` from the
checkout, which invoke `hermes update`. This means the *update channel
identity* is already fully Stardust-owned and does not block a rename —
but the **frozen installer binary** the docs call `hermes-setup.exe` has no
self-update path of its own, so a first-run rename has to reach users
through a fresh install of that binary, not through the git-pull updater
(see §3).

### 1d. Already-migrated (cosmetic, done)

`mac.extendInfo.CFBundleDisplayName: "Stardust"`, all `NSUsageDescription`
strings, `win.legalTrademarks: "Stardust"`, `linux.maintainer`/`synopsis`,
top-level `description`/`author`, `dmg.title: "Install Stardust"`, and the
protocol's declared `name: "Stardust Protocol"` (though its `schemes` array
is still `hermes`) are already Stardust. This confirms the package is
already in a deliberate, partial split-identity state — display-facing
strings moved first, installer/OS identity deliberately held back.

### 1e. Cosmetic strings fixed in this change (see §4 for why these are safe)

`main.ts` BrowserWindow `title` (4 call sites, main/session/next-instance
windows) and the two update-failure dialog titles/message
(`'Hermes update'` → `'Stardust update'`, `"Hermes couldn't finish
updating"` → `"Stardust couldn't finish updating"`) were renamed directly
in this PR. These are plain window-chrome/dialog text with no OS registry,
bundle-id, userData-path, or protocol tie-in.

Out of scope for this PR, flagged but *not* touched, because each is either
load-bearing or part of a much larger surface than "one string":

- `APP_NAME` default and the About panel's `applicationName`/`copyright`
  (the `APP_NAME` default, `app.setName`, About-panel metadata, and dependent window state) — changing the default
  changes the userData directory (§1b) and the copyright line is a legal/
  attribution call, not a pure rename.
- The built-in Hermes Cloud / Nous Portal desktop mode was retired on current `main` (#147). Remaining `Hermes Cloud` / `Nous Portal` strings are either persisted-connection compatibility labels, provider-auth terminology, tests, or dormant localization copy. They are not install-identity fields and should be handled under the product-independence/provider-compatibility work, not by renaming OS identity strings in this migration.
- `src/i18n/en.ts` (and the `zh`/`zh-hant`/`ru`/`ja` locale files) contain
  ~10 more "Hermes couldn't…" user-facing strings. This is a real, sizeable
  i18n copy pass across five locale files with its own review surface — it
  is not a one-line "genuinely zero-risk" fix and belongs in a dedicated
  copy-sweep PR, not folded into an installer-identity migration plan.

## 2. Classification

**Safe to change outright (pure display text, no OS/registry/userData tie-in):**
window titles, About-menu label text *if* decoupled from `APP_NAME`,
tray/menu labels (none currently exist — no `Tray(` usage found in
`apps/desktop/electron`), dialog titles, installer welcome/finish copy
(none custom today; inherited from electron-builder defaults, which are
already cosmetic-neutral).

**Load-bearing for existing installs (changing it makes the OS treat the
app as different, or orphans data):**

- `appId` (`com.nousresearch.hermes`) — macOS treats a `CFBundleIdentifier`
  change as a new app for Gatekeeper/notarization ticket association,
  keychain-item scoping, and (via `NSAppleEventsUsageDescription`-style TCC
  grants) permission grants; Windows treats it as a different AUMID (new
  taskbar grouping/notification identity, new Start Menu tile) unless
  explicitly aliased.
- `productName`/`executableName` — changes the default Electron `userData`
  path (unless `HERMES_DESKTOP_USER_DATA_DIR` is set), the install
  directory name, and therefore both `desktop-uninstall.ts`'s own
  self-detection regex and any OS shortcut/registry entry electron-builder
  wrote against the old name.
- protocol scheme `hermes` — every previously-saved `hermes://…` link
  (deep-link bookmarks, another app's "Add to Hermes" install button, a
  saved MCP-install URL) stops resolving the day the OS default-handler
  registration moves to a new scheme, unless the old scheme is kept
  registered too.
- `nsis.shortcutName`/`uninstallDisplayName`, `artifactName` — cosmetic on
  their own, but changing them independently of `appId`/`productName`
  produces a mismatched install (new shortcut name pointing at an
  unchanged AUMID/registry key), which is worse than not touching them yet.
- `com.nousresearch.hermes` in `tccutil` calls (`doctor_platform.py`,
  `main_desktop.py`, `update_cmd_maint.py`) and in
  `website/docs/user-guide/desktop.md` — these must track whatever the
  *actual* macOS bundle id is at runtime, or `hermes doctor`'s permission
  reset silently resets the wrong (or no) TCC entry after a real appId
  change.

**Needs a compatibility shim regardless of which option is chosen:**

- The `hermes://` protocol handler already has the right shape for this:
  `DEEPLINK_SCHEMES` is already an array; dev builds accept both `hermes-dev` and `hermes` while registering `hermes-dev` as the primary OS handler. Adding
  a new primary scheme is additive to this exact mechanism — register
  `stardust` as primary going forward while keeping `hermes` (and
  `hermes-dev`) in `DEEPLINK_SCHEMES` so an old saved link, another
  app's "Add to Hermes" button, or a bookmarked `hermes://blueprint/…`
  install continues to route into the running app indefinitely.
- `desktop-uninstall.ts`'s name-based install-dir detection needs to keep
  matching the *old* directory name for any machine that upgrades in place
  before a data-migration step (§3) has run, or the uninstaller stops
  finding a valid prior install to clean up.

## 3. Migration mechanism for the load-bearing pieces

Two realistic options, per the task brief:

- **(a) New app ID + first-launch data migration + updater hand-off.**
- **(b) Keep `appId` stable as a legacy technical identifier (like
  `HERMES_HOME` already is), rename every user-visible surface.**

### Recommendation: **(b), with a narrow, explicit exception**

Reasoning:

1. **There is no autoUpdater feed to break.** The single biggest reason
   electron-builder/electron-updater projects treat an `appId` change as
   catastrophic is that `electron-updater`'s update feed, code-signing
   validation, and Squirrel.Windows delta-patch matching are all keyed off
   `appId`/`productName` staying byte-for-byte stable across versions —
   change it and the running app can no longer find or trust its own next
   update (this is the failure mode electron-builder's own issue tracker
   documents repeatedly for appId changes). Stardust Desktop doesn't use
   that mechanism at all (§1c) — its update path is a git pull plus a
   PowerShell/shell hand-off that reads the *repo*, not an appId-keyed
   feed URL. That removes the single largest hazard option (a) exists to
   solve, and makes option (a)'s complexity (new-appID installer, an
   in-app "find my old userData and offer to import it" first-run flow,
   dual-track update hosting) a cost with no correspondingly large benefit
   here.
2. **`AGENTS.md`'s own precedent is to keep load-bearing inherited
   identifiers and migrate the visible surface around them** — exactly
   what already happened with `HERMES_HOME`, the `hermes` CLI, and (inside
   this very package.json) `CFBundleDisplayName` already saying `Stardust`
   while `CFBundleExecutable`/`appId` still say `Hermes`/
   `com.nousresearch.hermes`. Option (b) is not a new pattern for this
   codebase; it's continuing the one already chosen for every other
   inherited interface, and for the *other half* of this exact
   `package.json`.
3. **Option (a)'s main payoff — a clean new identity with no legacy
   string anywhere — is not worth the cost it re-introduces**: a
   first-launch "we found your old data, import it?" flow is itself a
   new, permanent piece of product surface (with its own bugs: partial
   imports, two app icons temporarily on a Dock/taskbar, users who decline
   the import and lose settings, TCC/keychain permissions that don't
   carry over on macOS regardless of any in-app import because they're
   tied to the OS-level bundle id, not to files Stardust controls). Option
   (b) has none of that: there is exactly one on-disk app, one Start Menu
   entry, one Gatekeeper ticket, continuously, across the rename.

**The narrow exception:** the protocol scheme *should* still move forward
(register `stardust://` as primary) because a URL scheme, unlike the
bundle id, is cheap to run two of side by side (§2's shim) and "Stardust
uses `hermes://` links" is a visibly wrong detail to leave permanently in
a fully-rebranded product, whereas "the macOS bundle id contains the word
hermes" is invisible to essentially all users. Keep `appId` stable
indefinitely (like `HERMES_HOME`); move the protocol scheme with a
permanent legacy-alias shim; move every other user-visible string now.

**If the maintainer later still wants a real `appId`/executable rename**
(e.g. distribution-store requirements, or dropping `nousresearch.com` from
the identifier entirely for legal reasons unrelated to branding), that
decision needs its own follow-up plan built on option (a)'s import flow —
this document deliberately does not attempt to design that flow, because
nothing in the current evidence requires it.

## 4. Sequencing

**Ships now, no migration mechanism needed (this PR):**

- `main.ts` BrowserWindow titles and update-dialog copy → done in this
  change (§1e).
- (Follow-up, small, separate PR — not bundled here to keep this PR's diff
  reviewable): the remaining `src/i18n/en.ts` + other-locale "Hermes
  couldn't…" strings, and the About panel's `applicationName`/copyright
  line once the maintainer picks the copyright wording (Stardust product
  copyright vs. retained Nous Research copyright — a decision this plan
  doesn't make unilaterally).

**Needs the compatibility mechanism from §3 before shipping:**

- Registering `stardust://` as the primary protocol scheme while keeping
  `hermes://`/`hermes-dev://` in `DEEPLINK_SCHEMES` (§2). This is low-risk
  *engineering* work (the array already exists) but must ship as one
  coordinated change across `main.ts`'s registration/parsing/reconstruction
  call sites (§1b) and the renderer consumers that hardcode the scheme
  string, with renderer coverage in `src/lib/hermes-open-target.test.ts`, `src/app/contrib/hooks/use-desktop-integrations.test.tsx`, and `src/store/native-notifications.test.ts`, plus a main-process registration/delivery test that proves both schemes resolve.
- `desktop-uninstall.ts`'s uninstall-summary regex would need a second
  pattern once/if any directory-name-facing string changes — currently
  nothing does, so no code change is needed yet, but this is the tripwire
  to re-check before touching `productName`.

**Deferred indefinitely under this plan's recommendation (§3):**
`appId`, `executableName`, `productName`'s effect on the install directory
name, `nsis.shortcutName`/`uninstallDisplayName`, `artifactName`,
`CFBundleExecutable`/`CFBundleName`, `setAppUserModelId`, and the
`tccutil`/`managed_uv.py` bundle-id references. These only move together,
and only if a future decision overrides §3's recommendation.

## 5. Test/verification plan

Per root `AGENTS.md`: "installers, and update paths need integration/E2E
proof when practical; mocks alone are not enough." For the pieces this
plan actually schedules (protocol-scheme dual-registration):

1. **Existing-install upgrade rehearsal**, extending the existing harness
   rather than inventing a new one: `apps/desktop/scripts/test-desktop.mjs`
   already has `existing` (launch with the real, already-installed app)
   and `fresh` (temp `userData` + `HERMES_HOME`) modes. Add a third
   rehearsal step that (a) packages the build with the new protocol
   registration, (b) launches it against a **pre-populated** `userData`
   directory checked into the test fixtures (a `connections.json`,
   `active-profile.json`, and `native-oauth-tokens.json` with known
   non-secret placeholder content) copied in place of a fresh sandbox, and
   (c) asserts every one of those files' contents survive the launch
   byte-for-byte except for fields the app is expected to touch
   (`window-state.json`, timestamps). This is the concrete way to "prove
   an existing install upgrades cleanly ... without losing conversation
   history, settings, or connections" — session/conversation history lives
   under `HERMES_HOME` (unaffected by any of this plan, since it's a
   separate compatibility-locked path), while desktop-local settings and
   connections live under the `userData` files above.
2. **Protocol dual-registration proof**: an E2E test (extending
   `e2e/remote-oauth-recovery.spec.ts`'s pattern of driving the packaged
   app, or the `connector-rehearsal.mjs` harness that already stubs
   `app.setAsDefaultProtocolClient`) that launches the app once with argv
   simulating a `hermes://blueprint/morning-brief` cold start and once
   with `stardust://blueprint/morning-brief`, asserting both deliver the
   same parsed `{kind, name, params}` payload to the renderer via
   `hermes:deep-link`. Cover both cold-start argv (Win/Linux) and
   `open-url` (macOS) delivery paths, matching the two code paths already
   in `main.ts`.
3. **Uninstall self-detection regression test**: a unit test on
   `desktop-uninstall.ts`'s path-matching helper asserting it still
   recognizes an install directory named after whatever `productName`
   currently is — this is cheap insurance so a future `productName` change
   (should §3's recommendation ever be revisited) can't silently make the
   uninstaller stop finding its own install without a test failing first.
4. **macOS TCC identity consistency check**: a source-level regression
   test (per root `AGENTS.md`'s guidance that source-text assertions are
   acceptable "for narrow repository-policy/source-authority invariants
   where executing every platform path is impractical") asserting that
   every `tccutil reset ... <bundle-id>` call site
   (`doctor_platform.py`, `main_desktop.py`, `update_cmd_maint.py`) and the
   documented value in `website/docs/user-guide/desktop.md` use the exact
   same literal as `package.json`'s `build.appId`, so the two can never
   drift independently even though this plan keeps that value stable.
