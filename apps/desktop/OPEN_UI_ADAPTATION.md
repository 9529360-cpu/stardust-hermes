# Open UI interaction adaptation - first candidate

Status: **uncommitted, not released, native Windows acceptance pending**.

Target: `9529360-cpu/stardust-hermes` at `9173f9a62f9b4781c58bec902dfabcb9b511548b`.
Reference: `Caho1/open-claude-ui` at `4045ce76afdcb7dccef1b7e6417b04c4c6046e9c`.
This is a selective adaptation, not a dependency on a second design system.
The existing Stardust theme, dimensions, state stores and business callbacks remain authoritative.

## Source-to-product mapping

| Reference implementation | Stardust production integration | Adaptation and check |
| --- | --- | --- |
| `code-examples/SegmentedControl.tsx` | `src/components/ui/segmented-control.tsx`, local CSS and navigation helper | One per-control indicator, 120ms positional transition, existing controlled `value/onChange`, roving keyboard focus and RTL. Selection does not wait for animation. No shared layout ID or new motion package. |
| `code-examples/ScrollFadeContainer.tsx` | `src/lib/scroll-fade.ts`, `src/hooks/use-scroll-fade.ts`, `ListColumn` / `DetailColumn` | Decorate the current native scrollport. Observe content and viewport size changes; coalesce reads in one animation frame. Never write scrollTop or the thread store. Focus/forced colors disable masking. |
| Same scroll reference | Existing `ScrollArea` | Optional `fadeEdges` prop, default false. Retain the Radix viewport and scrollbar. The main chat transcript is not opted in by this patch. |
| `code-examples/ArtifactsSplitPaneResizer.tsx` | `src/lib/pointer-resize.ts`, `src/hooks/use-pointer-resize.ts`, `MasterDetail` / `DetailPane` | Functional 4px dead-zone, pointer capture, hit-testable iframe shield, Escape / pointer cancel / blur / unmount cleanup. Local preview, one existing preference callback on release; no preference writes for cancellation. |
| Split-pane clamping / layout principle | `src/app/master-detail.css` | Both rail and wide layouts consume the saved width. Container-based stacking below 40rem, 180px list / 320px detail clamps when side-by-side. Live child content remains mounted. |

## Baseline issue reproduced

The original `MasterDetail` writes `--md-split` during drag but its ordinary rail
track is fixed at `14rem`; only the wide track reads the variable. Pointer moves
also call the persisted-width setter on every event, and Escape does not cancel
the gesture. An offline Chromium probe using the actual extracted original
handlers reproduced these behaviors, then exercised the candidate handlers.

The reference is not copied mechanically: a `pointer-events:none` overlay does
not shield an embedded frame, and a shared animation layout ID can couple
unrelated controls. This adaptation uses a hit-testable local gesture shield
and independent CSS indicator geometry instead. No reference fonts, artwork,
audio, brand colors, model roster or business logic are imported.

## Scope and compatibility

No backend, gateway, IPC, model routing, approval semantics, session identity,
project ownership or native browser controller was changed. Existing pane
preference keys and actions are retained. The main chat composer, sticky prompt,
thread scroll restoration and approval jump path are deliberately untouched.
This candidate does not claim to migrate all thirty reference components.
There is no new npm dependency or lockfile change.

Shared primitive contracts are updated in `DESIGN.md` in the same patch.
Attribution and the reference MIT notice are in
`THIRD_PARTY_NOTICES/open-claude-ui.txt`; the root license is unchanged.

## Evidence obtained in this environment

- Three pre-existing edited source files were reconstructed from pinned GitHub
  reads and matched their Git blob SHA values byte for byte.
- The three dependency-free production modules passed strict TypeScript 5.8
  checking against the DOM and ES2022 libraries. This is **not** workspace typecheck.
- Offline Chromium: 17 checks passed, including the original regression,
  drag preview/release/cancel, 30 repeated cancels, six pane widths, native iframe
  hit testing, keyboard/RTL, reduced motion, scroll edges and observer cleanup.
- Node assertions: 20 directed pure-function cases, 2,541 scroll geometry inputs
  and 272 keyboard navigation round-trips passed.
- Four repository Vitest regression suites are included for the intended runtime.
  **Those suites have not been run here**, because workspace dependencies cannot
  be installed in this environment.

The browser fixture uses the actual stateless selector TSX and production DOM
controllers / extracted handler bodies, but has test-double preference callbacks,
fixture data and fixture theme values. Its locally available React 16.0 and
Tailwind 4.1 differ from the repository's React 19.2.7 and Tailwind 4.3.3.
It does not mount the full React application or verify nanostore persistence,
Electron IPC, a native WebView, actual model streaming, or real conversation load.
Screenshots are explicitly labeled as component fixtures, not product screenshots.

## Required acceptance before commit or push

Install dependencies using the repository-root workspace/lockfile policy, then
run from `apps/desktop`:

```sh
npx vitest run src/components/ui/segmented-control-navigation.test.ts src/components/ui/segmented-control.test.tsx src/lib/scroll-fade.test.ts src/lib/pointer-resize.test.ts
npm run typecheck
npm run lint
```

These commands are the **remaining acceptance plan**, not claims of execution.
Also run the affected existing tests and the full appropriate UI test lane.

Launch the candidate as a **native Windows Electron development instance** using
an isolated task `TEMP/TMP`, `HERMES_HOME` and `STARDUST_DESKTOP_USER_DATA_DIR`.
Do not use, overwrite or restart the installed personal instance. Record the
candidate hash, data directories, process identity, disk bounds and final evidence.
Validate actual Capabilities/Messaging pages and settings controls, including:

- Ordinary and wide column resize, double-click reset, small movement, Escape,
  missing pointerup, resize during drag, page close/unmount and rapid repetitions.
  Cancel must not persist; release must survive a restart under the same scope.
- Container widths around 40rem, changed UI scale, both themes, long translated
  labels, keyboard focus, reduced motion and forced colors.
- Dynamic list/detail content, focus at a faded edge, images/disclosures and
  native embedded content under the resize shield.
- Existing chatting, IME entry, streaming, approvals, long conversation scroll,
  project navigation, browser and multi-agent behavior as regression checks.

Root `AGENTS.md` makes native acceptance mandatory before commit/push. This
candidate is not permitted to bypass that gate via mock tests, a browser preview,
a CI run, a different host platform, or a visual-only screenshot.
