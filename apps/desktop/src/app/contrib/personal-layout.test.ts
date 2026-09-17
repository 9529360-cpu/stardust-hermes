import { describe, expect, it } from 'vitest'

import { allPaneIds } from '@/components/pane-shell/tree/model'

import { DEFAULT_TREE } from './layout-presets'

describe('Stardust Codex-style default layout', () => {
  it('boots into projects/threads, active workspace and Review', () => {
    expect(allPaneIds(DEFAULT_TREE)).toEqual(['sessions', 'workspace', 'review'])
  })

  it.each(['terminal', 'files'])('keeps %s available on demand instead of permanently occupying first view', paneId => {
    expect(allPaneIds(DEFAULT_TREE)).not.toContain(paneId)
  })

  it('makes Review the dedicated chromeless right work surface', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type !== 'split') {
      return
    }

    const review = DEFAULT_TREE.children[2]

    expect(review.type).toBe('group')

    if (review.type === 'group') {
      expect(review.id).toBe('grp-review')
      expect(review.panes).toEqual(['review'])
      expect(review.tabStrip).toBe('never')
    }
  })

  it('keeps the permanent left and center product regions free of generic IDE tabs', () => {
    expect(DEFAULT_TREE.type).toBe('split')

    if (DEFAULT_TREE.type !== 'split') {
      return
    }

    for (const region of DEFAULT_TREE.children) {
      expect(region.type).toBe('group')

      if (region.type === 'group') {
        expect(region.tabStrip).toBe('never')
      }
    }
  })
})
