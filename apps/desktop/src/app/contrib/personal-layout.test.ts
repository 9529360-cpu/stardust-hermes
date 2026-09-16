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

describe('Stardust desktop default layout', () => {
  it('boots into only the three permanent product regions', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual(['sessions', 'workspace', WORKSPACE_OVERVIEW_PANE_ID])
  })

  it.each(['terminal', 'review', 'files'])('keeps %s out of the first view', paneId => {
    expect(allPaneIds(DEFAULT_TREE)).not.toContain(paneId)
  })

  it('keeps the inspector as a dedicated right rail instead of an IDE tab stack', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type !== 'split') {
      return
    }

    const context = DEFAULT_TREE.children[2]

    expect(context.type).toBe('group')

    if (context.type === 'group') {
      expect(context.panes).toEqual([WORKSPACE_OVERVIEW_PANE_ID])
    }
  })

  it('registers the inspector as fixed core product chrome without a pane tab strip', () => {
    disposeOverview = registerWorkspaceOverviewPane()

    const overview = registry.getArea('panes').find(pane => pane.id === WORKSPACE_OVERVIEW_PANE_ID)
    const data = overview?.data as { headerVeto?: boolean; uncloseable?: boolean } | undefined

    expect(overview?.source).toBe('core')
    expect(data?.uncloseable).toBe(true)
    expect(data?.headerVeto).toBe(true)
  })
})
