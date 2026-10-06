import { describe, expect, it } from 'vitest'

import { allPaneIds } from '@/components/pane-shell/tree/model'

import { DEFAULT_TREE } from './layout-presets'

describe('personal desktop default layout', () => {
  it('keeps chat dominant with conversations and on-demand tools on the sides', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual(['sessions', 'workspace', 'review', 'files'])
  })

  it('keeps developer tools and the retired overview out of the first view', () => {
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('terminal')
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('workspace-overview')
  })

  it('hosts review and files in one right rail without the overview tab', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type !== 'split') {
      return
    }

    const context = DEFAULT_TREE.children[2]

    expect(context.type).toBe('group')

    if (context.type === 'group') {
      expect(context.panes).toEqual(['review', 'files'])
    }
  })
})
