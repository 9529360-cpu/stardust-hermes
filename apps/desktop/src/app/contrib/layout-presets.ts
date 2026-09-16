import { group, split } from '@/components/pane-shell/tree/model'
import { registry } from '@/contrib/registry'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'

import {
  registerWorkspaceOverviewPane,
  schedulePersonalLayoutMigration,
  WORKSPACE_OVERVIEW_PANE_ID
} from './workspace-overview'

// Stardust's default shell has three permanent product regions only:
// task rail / task workspace / context inspector. Files, Review and Terminal
// remain real tools, but they are summoned on demand instead of creating IDE
// tabs in the first frame.
export const DEFAULT_TREE = split(
  'row',
  [
    group(['sessions'], { id: 'grp-sessions' }),
    group(['workspace'], { id: 'grp-main' }),
    group([WORKSPACE_OVERVIEW_PANE_ID], { id: 'grp-context' })
  ],
  [1.05, 4.15, 1.45],
  'spl-root'
)

const FOCUS_TREE = split(
  'row',
  [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])],
  [0.9, 5.2, 1.25]
)

const BASIC_TREE = split(
  'row',
  [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])],
  [1, 4.6, 1.25]
)

const TERMINAL_TREE = split(
  'column',
  [
    split(
      'row',
      [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])],
      [1.05, 4, 1.4]
    ),
    group(['terminal'])
  ],
  [3.4, 1]
)

const QUAD_TREE = split(
  'column',
  [
    split('row', [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])], [1, 3.8, 1.35]),
    split('row', [group(['terminal']), group(['review', 'files'])], [1.8, 1])
  ],
  [3.2, 1]
)

export function registerLayoutPresets() {
  registerWorkspaceOverviewPane()

  const dispose = registry.registerMany([
    { id: 'default', area: 'layouts', title: 'Default', order: 0, data: DEFAULT_TREE },
    ...(isOnboardingEnabled() ? [{ id: 'basic', area: 'layouts', title: 'Basic', order: 5, data: BASIC_TREE }] : []),
    { id: 'focus', area: 'layouts', title: 'Focus', order: 10, data: FOCUS_TREE },
    { id: 'terminal-deck', area: 'layouts', title: 'Terminal deck', order: 20, data: TERMINAL_TREE },
    { id: 'quad', area: 'layouts', title: 'Quad', order: 30, data: QUAD_TREE }
  ])

  schedulePersonalLayoutMigration()

  return dispose
}
