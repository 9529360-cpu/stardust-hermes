// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree, noteActiveTreeGroup } from '@/components/pane-shell/tree/store'
import { SidebarProvider } from '@/components/ui/sidebar'
import { registry } from '@/contrib/registry'
import { $selectedStoredSessionId, $sessions, $sessionsLoading } from '@/store/session'
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

const expectOnlySelectedSession = (title: null | string) => {
  const selectedRows = ['Tile one', 'Tile two']
    .map(label => screen.queryByText(label)?.closest('.group.row-hover'))
    .filter(row => row?.className.includes('bg-(--ui-row-active-background)'))

  expect(selectedRows).toEqual(title ? [screen.getByText(title).closest('.group.row-hover')] : [])
}

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
    $sessionsLoading.set(true)
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
  })

  it('keeps session selection coherent with the focused pane', () => {
    renderSidebar('/kanban', 'extension')
    expectOnlySelectedSession(null)

    focus('tile-one-group')
    expectOnlySelectedSession('Tile one')

    focus('tile-two-group')
    expectOnlySelectedSession('Tile two')

    focus(null)
    expectOnlySelectedSession(null)

    // Removing the focused tile's session leaves nothing selected rather than
    // jumping the highlight to a row the user never focused.
    focus('tile-two-group')
    act(() => {
      $removedSessionIds.set(new Set(['tile-two']))
      $sessions.set([sessionRows[0]])
    })
    expectOnlySelectedSession(null)
  })

  it('keeps the conversation list visible while a product page owns the workspace', () => {
    for (const [pathname, currentView] of [
      ['/skills?tab=toolsets', 'skills'],
      ['/skills?tab=plugins', 'skills'],
      ['/messaging', 'messaging'],
      ['/artifacts', 'artifacts'],
      ['/cron', 'cron']
    ] as const) {
      cleanup()
      focus('workspace-group')
      renderSidebar(pathname, currentView)

      expect(screen.getByText('Tile one')).toBeTruthy()
      expect(screen.getByText('Tile two')).toBeTruthy()
      expect(screen.queryByRole('textbox', { name: 'Search sessions' })).toBeNull()
      expectOnlySelectedSession(null)

      focus('tile-one-group')
      expectOnlySelectedSession('Tile one')
    }
  })

  it('keeps the pinned and conversation sections when history is empty', () => {
    // A loaded-but-empty history. `$sessionsLoading` starts true (before the
    // first list fetch), which correctly paints skeletons instead of the copy.
    act(() => {
      $sessions.set([])
      $sessionsLoading.set(false)
    })

    renderSidebar('/', 'chat')

    expect(screen.getByText('Pinned chats')).toBeTruthy()
    expect(screen.getByText('Chats')).toBeTruthy()
    expect(screen.getByText('No sessions yet')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'New project' })).toBeNull()
  })
})
