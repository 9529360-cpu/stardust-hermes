import { group, split } from '@/components/pane-shell/tree/model'
import { registry } from '@/contrib/registry'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'

import { registerWorkspaceOverviewPane, WORKSPACE_OVERVIEW_PANE_ID } from './workspace-overview'

// Private-product default: conversations on the left, the active chat as the
// dominant surface, and one calm context rail on the right. Files and Review
// live as tabs in that context rail and stay hidden until the user asks for
// them; the always-available overview keeps the rail useful when no tool pane
// is open. Terminal is intentionally absent from the first view and appears on
// demand.
export const DEFAULT_TREE = split(
  'row',
  [
    group(['sessions'], { id: 'grp-sessions' }),
    group(['workspace'], { id: 'grp-main' }),
    group([WORKSPACE_OVERVIEW_PANE_ID, 'review', 'files'], { id: 'grp-context' })
  ],
  [1, 3.5, 1.2],
  'spl-root'
)

const FOCUS_TREE = split(
  'row',
  [group(['sessions']), group(['workspace', WORKSPACE_OVERVIEW_PANE_ID, 'files', 'review', 'terminal'])],
  [1, 4.6]
)

const BASIC_TREE = split(
  'row',
  [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])],
  [1, 3.8, 1.05]
)

const TERMINAL_TREE = split(
  'column',
  [
    split(
      'row',
      [group(['sessions']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID, 'files', 'review'])],
      [1, 3.2, 1.2]
    ),
    group(['terminal'])
  ],
  [3, 1]
)

const QUAD_TREE = split(
  'column',
  [
    split('row', [group(['sessions', 'files']), group(['workspace']), group([WORKSPACE_OVERVIEW_PANE_ID])], [1, 3, 1.1]),
    split('row', [group(['terminal']), group(['review'])], [1.4, 1])
  ],
  [3, 1]
)

export function registerLayoutPresets() {
  // The overview is product chrome, not an optional plugin: it must exist
  // whenever a preset references it.
  registerWorkspaceOverviewPane()

  return registry.registerMany([
    { id: 'default', area: 'layouts', title: 'Default', order: 0, data: DEFAULT_TREE },
    ...(isOnboardingEnabled() ? [{ id: 'basic', area: 'layouts', title: 'Basic', order: 5, data: BASIC_TREE }] : []),
    { id: 'focus', area: 'layouts', title: 'Focus', order: 10, data: FOCUS_TREE },
    { id: 'terminal-deck', area: 'layouts', title: 'Terminal deck', order: 20, data: TERMINAL_TREE },
    { id: 'quad', area: 'layouts', title: 'Quad', order: 30, data: QUAD_TREE }
  ])
}
