# Open UI interaction adaptation - first candidate

Status: **native Windows Electron acceptance passed on the PR #481 integration candidate; not merged or released**.

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
- Full workspace checks on the conflict-resolved candidate: `npm run typecheck`,
  `npm run lint`, and `npm run test:ui` passed; the UI project ran 9,076 tests
  across 893 files. `npm run build` passed.
- Native Windows Electron Playwright: five tests passed against the built
  candidate. They exercised task navigation at wide/narrow sizes, pending
  clarify navigation/return, background completion after route remount, split
  resize commit/reset/cancel/window resize, and scroll fades at real list edges.
  The E2E fixtures used isolated task `TEMP/TMP`, Hermes home and Electron
  user-data directories; they used a local mock gateway and touched no installed
  user instance.
- `npm run test:desktop:platforms` had two Windows failures outside this patch:
  the symlink-mode contract and the POSIX `sun_path` length assumption. The same
  two exact tests fail on current `main` (`ce65f332`); their implementation and
  test files are unchanged by this PR. The remaining platform test cases passed.

## Acceptance record

Validated on native Windows 11 against the conflict-resolved PR #481 source tree
`91fb2428f5e53b47591b6468fd7d616fec6050d4` (the only later change here is this
verification-document update):

```sh
npm run typecheck
npm run lint
npm run test:ui
npm run build
npm exec -- playwright test --workers=1 --output <task-temp>/playwright-results-final3 ui-adaptation.spec.ts task-center.spec.ts
```

- Typecheck, lint, build, and the complete UI project passed (9,076 tests across
  893 files).
- Five native Electron scenarios passed: task navigation at wide/narrow sizes,
  clarify-task return to its owner, background completion after route remount,
  split resizing/cancel/window resize, and scroll fades at real content edges.
- E2E data lived under `D:\GPTcodex\Temp\Stardust\task-center-pr481-20261020`; the
  Electron fixture used isolated `HERMES_HOME` and `STARDUST_DESKTOP_USER_DATA_DIR`.
  Screenshots and the test log are retained in
  `Reports/Stardust/handoff-bugs-20261020/pr481-final-acceptance/`.
- `npm run test:desktop:platforms` had two failing Windows-only baseline assertions
  in `electron/hardening.test.ts` (POSIX symlink-mode return value) and
  `electron/ssh-connection.test.ts` (POSIX `sun_path` length). Both exact tests
  fail on current main `ce65f332`; these files are unchanged by PR #481. The other
  desktop-platform cases passed.

This is native Electron acceptance, not a browser-only preview. The installed
personal client and its data were not started, overwritten, or restarted. The
updated PR still needs its post-merge CI checks to finish before it can be merged.
