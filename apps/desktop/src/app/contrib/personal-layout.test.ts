import { describe, expect, it } from 'vitest'

import { allPaneIds } from '@/components/pane-shell/tree/model'

import { DEFAULT_TREE } from './layout-presets'

describe('personal desktop default layout', () => {
  it('keeps chat dominant with conversations and workspace context on the sides', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual([
      'sessions',
      'workspace',
      'personal-shell:overview',
      'review',
      'files'
    ])
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
      expect(context.panes).toEqual(['personal-shell:overview', 'review', 'files'])
    }
  })
})
