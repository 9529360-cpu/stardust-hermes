import { afterEach, describe, expect, it } from 'vitest'

import { allPaneIds } from '@/components/pane-shell/tree/model'
import { registry } from '@/contrib/registry'

import { DEFAULT_TREE } from './layout-presets'
import { registerWorkspaceOverviewPane, WORKSPACE_OVERVIEW_PANE_ID } from './workspace-overview'

let disposeOverview: (() => void) | null = null

afterEach(() => {
  disposeOverview?.()
  disposeOverview = null
})

describe('personal desktop default layout', () => {
  it('keeps chat dominant with conversations and workspace context on the sides', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual(['sessions', 'workspace', WORKSPACE_OVERVIEW_PANE_ID, 'review', 'files'])
  })

  it('keeps developer tools out of the first view', () => {
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('terminal')
  })

  it('hosts overview, review and files in one contextual right rail', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type !== 'split') {
      return
    }

    const context = DEFAULT_TREE.children[2]

    expect(context.type).toBe('group')

    if (context.type === 'group') {
      expect(context.panes).toEqual([WORKSPACE_OVERVIEW_PANE_ID, 'review', 'files'])
    }
  })

  it('registers the overview as fixed core product chrome', () => {
    disposeOverview = registerWorkspaceOverviewPane()

    const overview = registry.getArea('panes').find(pane => pane.id === WORKSPACE_OVERVIEW_PANE_ID)

    expect(overview?.source).toBe('core')
    expect((overview?.data as { uncloseable?: boolean } | undefined)?.uncloseable).toBe(true)
  })
})
