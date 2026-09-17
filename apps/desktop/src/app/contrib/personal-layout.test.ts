import { describe, expect, it } from 'vitest'

import { allPaneIds } from '@/components/pane-shell/tree/model'

import { DEFAULT_TREE } from './layout-presets'

describe('developer desktop default layout', () => {
  it('ships as a project rail plus one dominant agent workspace', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual(['sessions', 'workspace'])
  })

  it('keeps review, files and terminal out of the first view', () => {
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('review')
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('files')
    expect(allPaneIds(DEFAULT_TREE)).not.toContain('terminal')
  })

  it('gives the agent workspace most of the horizontal surface', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type === 'split') {
      expect(DEFAULT_TREE.orientation).toBe('row')
      expect(DEFAULT_TREE.weights[1]).toBeGreaterThan(DEFAULT_TREE.weights[0] * 4)
    }
  })
})
