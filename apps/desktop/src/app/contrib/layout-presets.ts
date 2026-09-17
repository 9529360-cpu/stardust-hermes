import { group, split } from '@/components/pane-shell/tree/model'
import { $activePresetId, resetLayoutTree } from '@/components/pane-shell/tree/store'
import { registry } from '@/contrib/registry'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'
import { readKey, writeKey } from '@/lib/storage'
import { isAuxiliaryWindow } from '@/store/windows'

const CODEX_SHELL_MIGRATION_KEY = 'stardust.desktop.codexShellLayout.v1'

// Stardust's primary desktop is a developer command center: project/task
// navigation on the left, the active agent thread as the dominant surface.
// Diff, files and terminal are task tools revealed on demand instead of a
// permanent dashboard column competing with the work itself.
export const DEFAULT_TREE = split(
  'row',
  [group(['sessions'], { id: 'grp-sessions' }), group(['workspace'], { id: 'grp-main' })],
  [1, 4.2],
  'spl-root'
)

const BASIC_TREE = split('row', [group(['sessions']), group(['workspace'])], [1, 4])
const FOCUS_TREE = split('row', [group(['sessions']), group(['workspace'])], [1, 5])

const TERMINAL_TREE = split(
  'column',
  [split('row', [group(['sessions']), group(['workspace'])], [1, 4]), group(['terminal'])],
  [3.2, 1]
)

const QUAD_TREE = split(
  'column',
  [
    split('row', [group(['sessions']), group(['workspace']), group(['review'])], [1, 3.6, 1.35]),
    split('row', [group(['terminal']), group(['files'])], [2, 1])
  ],
  [3, 1]
)

function scheduleCodexShellLayoutMigration() {
  if (isAuxiliaryWindow() || readKey(CODEX_SHELL_MIGRATION_KEY) === 'done') {
    return
  }

  queueMicrotask(() => {
    // Preserve hand-built layouts. `basic` was the old onboarding-owned shipped
    // preset, so it migrates with `default`; anything custom stays untouched.
    if (['default', 'basic'].includes($activePresetId.get())) {
      resetLayoutTree()
    }

    writeKey(CODEX_SHELL_MIGRATION_KEY, 'done')
  })
}

export function registerLayoutPresets() {
  const dispose = registry.registerMany([
    { id: 'default', area: 'layouts', title: 'Default', order: 0, data: DEFAULT_TREE },
    ...(isOnboardingEnabled() ? [{ id: 'basic', area: 'layouts', title: 'Basic', order: 5, data: BASIC_TREE }] : []),
    { id: 'focus', area: 'layouts', title: 'Focus', order: 10, data: FOCUS_TREE },
    { id: 'terminal-deck', area: 'layouts', title: 'Terminal deck', order: 20, data: TERMINAL_TREE },
    { id: 'quad', area: 'layouts', title: 'Quad', order: 30, data: QUAD_TREE }
  ])

  scheduleCodexShellLayoutMigration()

  return dispose
}
