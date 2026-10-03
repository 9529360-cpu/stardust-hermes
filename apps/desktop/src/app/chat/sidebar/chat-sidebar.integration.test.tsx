// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree, noteActiveTreeGroup } from '@/components/pane-shell/tree/store'
import { SidebarProvider } from '@/components/ui/sidebar'
import { registry } from '@/contrib/registry'
import { $selectedStoredSessionId, $sessions } from '@/store/session'
import { $removedSessionIds } from '@/store/session-removal'
import { makeSessionInfo } from '@/test/session-info'

import { type AppView, ROUTES_AREA, SIDEBAR_NAV_AREA } from '../../routes'

import { ChatSidebar } from './index'

const noop = () => {}
const sessionRows = [
  makeSessionInfo({ id: 'tile-one', last_active: 2, profile: 'default', started_at: 1, title: 'Tile one' }),
  makeSessionInfo({ id: 'tile-two', last_active: 2, profile: 'default', started_at: 1, title: 'Tile two' })
]

const renderSidebar = (pathname: string, currentView: AppView) =>
  render(
    <MemoryRouter initialEntries={[pathname]}>
      <SidebarProvider>
        <ChatSidebar
          currentView={currentView}
          onArchiveSession={noop}
          onBranchSession={noop}
          onDeleteSession={noop}
          onLoadMoreSessions={noop}
          onNewSessionInWorkspace={noop}
          onNewSessionSplit={noop}
          onResumeSession={noop}
        />
      </SidebarProvider>
    </MemoryRouter>
  )

const selectedRows = () =>
  ['Tile one', 'Tile two']
    .map(label => screen.queryByText(label)?.closest('.group.row-hover'))
    .filter(row => row?.className.includes('bg-(--ui-row-active-background)'))

const focus = (groupId: null | string) => act(() => noteActiveTreeGroup(groupId))

describe('ChatSidebar compact conversation surface', () => {
  let disposeContributions: () => void

  beforeEach(() => {
    disposeContributions = registry.registerMany([
      { area: ROUTES_AREA, id: 'kanban-page', data: { path: '/kanban' }, render: () => null },
      { area: SIDEBAR_NAV_AREA, id: 'kanban-nav', data: { codicon: 'project', label: 'Kanban', path: '/kanban' } }
    ])
    $selectedStoredSessionId.set('tile-one')
    $sessions.set(sessionRows)
    $removedSessionIds.set(new Set())
    $layoutTree.set(
      split('row', [
        group(['workspace'], { active: 'workspace', id: 'workspace-group' }),
        group(['session-tile:tile-one'], { active: 'session-tile:tile-one', id: 'tile-one-group' }),
        group(['session-tile:tile-two'], { active: 'session-tile:tile-two', id: 'tile-two-group' })
      ])
    )
    noteActiveTreeGroup('workspace-group')
  })

  afterEach(() => {
    cleanup()
    disposeContributions()
    $selectedStoredSessionId.set(null)
    $sessions.set([])
    $removedSessionIds.set(new Set())
    $layoutTree.set(null)
    noteActiveTreeGroup(null)
  })

  it('shows pinned/conversation content without feature nav or persistent search', () => {
    renderSidebar('/kanban', 'extension')

    expect(screen.getByText('Pinned chats')).toBeTruthy()
    expect(screen.getByText('Tile one')).toBeTruthy()
    expect(screen.getByText('Tile two')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Kanban' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Messaging' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Artifacts' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Scheduled jobs' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'Search sessions' })).toBeNull()

    focus('tile-one-group')
    expect(selectedRows()).toEqual([screen.getByText('Tile one').closest('.group.row-hover')])
  })

  it('keeps the conversation list visible while a product page owns the workspace', () => {
    renderSidebar('/skills?tab=plugins', 'skills')

    expect(screen.getByText('Tile one')).toBeTruthy()
    expect(screen.getByText('Tile two')).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: 'Search sessions' })).toBeNull()
  })
})
