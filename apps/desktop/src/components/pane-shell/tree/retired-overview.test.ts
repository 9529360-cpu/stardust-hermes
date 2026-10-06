import { describe, expect, it } from 'vitest'

import { allPaneIds, findGroup, group, split } from './model'
import { withoutRetiredOverview } from './store'

describe('retired overview layout migration', () => {
  it('removes only the old overview tab and selects its surviving neighbor', () => {
    const saved = split('row', [
      group(['sessions'], { id: 'left' }),
      group(['workspace', 'session-tile:1'], { id: 'main', active: 'session-tile:1' }),
      group(['workspace-overview', 'review', 'files', 'preview-tile:1'], {
        id: 'right', active: 'workspace-overview'
      })
    ])
    const result = withoutRetiredOverview(saved)

    expect(result && allPaneIds(result)).toEqual(['sessions', 'workspace', 'session-tile:1', 'review', 'files', 'preview-tile:1'])
    expect(result && findGroup(result, 'right')).toMatchObject({ active: 'review', panes: ['review', 'files', 'preview-tile:1'] })
    expect(result && findGroup(result, 'main')).toMatchObject({ active: 'session-tile:1' })
  })

  it('collapses a retired-only zone without discarding the rest of the tree', () => {
    const saved = split('row', [group(['workspace']), group(['workspace-overview'])])
    expect(withoutRetiredOverview(saved)).toMatchObject({ type: 'group', panes: ['workspace'] })
  })
})
