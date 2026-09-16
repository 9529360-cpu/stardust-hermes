import { group, split } from '@/components/pane-shell/tree/model'
import { registry } from '@/contrib/registry'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'

import { schedulePersonalLayoutMigration } from './layout-migration'

const productGroup = (panes: string[], id?: string) => group(panes, { id, tabStrip: 'never' })

/**
 * Codex-style default shell:
 *
 *   Projects / Threads | active thread workspace | Review
 *
 * Review is not a dashboard card stack — it is the real git work surface.
 * Terminal stays a bottom tool pane and Files stays an on-demand right tool;
 * both retain the pane-tree's sash resizing, collapse and persistence.
 */
export const DEFAULT_TREE = split(
  'row',
  [
    productGroup(['sessions'], 'grp-sessions'),
    productGroup(['workspace'], 'grp-main'),
    productGroup(['review'], 'grp-review')
  ],
  [1.05, 3.9, 1.95],
  'spl-root'
)

const FOCUS_TREE = split(
  'row',
  [productGroup(['sessions']), productGroup(['workspace']), productGroup(['review'])],
  [0.9, 4.8, 1.7]
)

const BASIC_TREE = split(
  'row',
  [productGroup(['sessions']), productGroup(['workspace']), productGroup(['review'])],
  [1, 4.25, 1.75]
)

const TERMINAL_TREE = split(
  'column',
  [
    split(
      'row',
      [productGroup(['sessions']), productGroup(['workspace']), productGroup(['review'])],
      [1.05, 3.9, 1.95]
    ),
    group(['terminal'])
  ],
  [3.5, 1]
)

const QUAD_TREE = split(
  'column',
  [
    split(
      'row',
      [productGroup(['sessions']), productGroup(['workspace']), productGroup(['review'])],
      [1, 3.7, 1.8]
    ),
    split('row', [group(['terminal']), group(['files'])], [1.9, 1])
  ],
  [3.25, 1]
)

export function registerLayoutPresets() {
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
