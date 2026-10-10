import { group, split } from '@/components/pane-shell/tree/model'
import { registry } from '@/contrib/registry'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'

// Conversations on the left, chat in the center, and files/review on demand
// in the right rail. The retired overview tab is removed from saved layouts
// when the tree is loaded, without disturbing other user-placed panes.
// Terminal is intentionally absent from the first view and appears on demand.
export const DEFAULT_TREE = split(
  'row',
  [
    group(['sessions'], { id: 'grp-sessions' }),
    group(['workspace'], { id: 'grp-main' }),
    group(['review', 'files'], { id: 'grp-context' })
  ],
  [0.82, 3.9, 1.45],
  'spl-root'
)

const FOCUS_TREE = split(
  'row',
  [group(['sessions']), group(['workspace', 'files', 'review', 'terminal'])],
  [1, 4.6]
)

const BASIC_TREE = split(
  'row',
  [group(['sessions']), group(['workspace']), group(['files', 'review'])],
  [0.82, 4.4, 1.05]
)

const TERMINAL_TREE = split(
  'column',
  [
    split(
      'row',
      [group(['sessions']), group(['workspace']), group(['files', 'review'])],
      [1, 3.2, 1.2]
    ),
    group(['terminal'])
  ],
  [3, 1]
)

const QUAD_TREE = split(
  'column',
  [
    split('row', [group(['sessions']), group(['workspace']), group(['files'])], [1, 3, 1.1]),
    split('row', [group(['terminal']), group(['review'])], [1.4, 1])
  ],
  [3, 1]
)

export function registerLayoutPresets() {
  const dispose = registry.registerMany([
    { id: 'default', area: 'layouts', title: 'Default', order: 0, data: DEFAULT_TREE },
    ...(isOnboardingEnabled() ? [{ id: 'basic', area: 'layouts', title: 'Basic', order: 5, data: BASIC_TREE }] : []),
    { id: 'focus', area: 'layouts', title: 'Focus', order: 10, data: FOCUS_TREE },
    { id: 'terminal-deck', area: 'layouts', title: 'Terminal deck', order: 20, data: TERMINAL_TREE },
    { id: 'quad', area: 'layouts', title: 'Quad', order: 30, data: QUAD_TREE }
  ])

  return dispose
}
