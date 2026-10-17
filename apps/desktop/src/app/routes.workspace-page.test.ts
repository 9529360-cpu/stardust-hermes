import { afterEach, describe, expect, it, vi } from 'vitest'

import { $workspaceIsPage, appViewForPath, navigateToWorkspacePage } from './routes'

const { noteActiveTreeGroup, revealTreePane } = vi.hoisted(() => ({
  noteActiveTreeGroup: vi.fn(),
  revealTreePane: vi.fn()
}))

vi.mock('@/components/pane-shell/tree/store', async importOriginal => {
  const actual = await importOriginal<Record<string, unknown>>()

  return { ...actual, noteActiveTreeGroup, revealTreePane }
})

describe('navigateToWorkspacePage', () => {
  afterEach(() => {
    $workspaceIsPage.set(false)
    noteActiveTreeGroup.mockClear()
    revealTreePane.mockClear()
  })

  it('classifies the nav-owned Agent Space path as a workspace page before plugin registration', () => {
    expect(appViewForPath('/bots')).toBe('extension')
  })

  it('restores a contributed workspace page when its route is already selected', () => {
    const navigate = vi.fn()
    $workspaceIsPage.set(false)

    navigateToWorkspacePage(navigate, '/skills')

    expect(navigate).toHaveBeenCalledWith('/skills', undefined)
    expect($workspaceIsPage.get()).toBe(true)
    expect(noteActiveTreeGroup).toHaveBeenCalledWith(null)
    expect(revealTreePane).toHaveBeenCalledWith('workspace')
  })
})
