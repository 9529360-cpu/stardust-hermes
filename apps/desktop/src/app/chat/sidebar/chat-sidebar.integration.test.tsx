// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree, noteActiveTreeGroup } from '@/components/pane-shell/tree/store'
import { SidebarProvider } from '@/components/ui/sidebar'
import { registry } from '@/contrib/registry'
import { $pinnedSessionIds } from '@/store/layout'
import { $projects } from '@/store/projects'
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

const renderSidebar = (pathname: string, currentView: AppView, projectChatsInNav = false) =>
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
          projectChatsInNav={projectChatsInNav}
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
    $projects.set([])
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
    $projects.set([])
    $sessionsLoading.set(true)
    $removedSessionIds.set(new Set())
    $pinnedSessionIds.set([])
    $layoutTree.set(null)
    noteActiveTreeGroup(null)
  })

  it('shows pinned/conversation content without feature nav or persistent search', () => {
    renderSidebar('/kanban', 'extension')

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

  it('keeps the conversation section when history is empty, with no empty Pinned section', () => {
    // A loaded-but-empty history. `$sessionsLoading` starts true (before the
    // first list fetch), which correctly paints skeletons instead of the copy.
    act(() => {
      $sessions.set([])
      $sessionsLoading.set(false)
    })

    renderSidebar('/', 'chat')

    expect(screen.queryByText('Pinned chats')).toBeNull()
    expect(screen.getByText('Chats')).toBeTruthy()
    expect(screen.getByText('No sessions yet')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'New project' })).toBeNull()
  })

  it('shows the Pinned section once a chat is pinned, holding that chat', () => {
    act(() => {
      $sessionsLoading.set(false)
      $pinnedSessionIds.set(['tile-two'])
    })

    renderSidebar('/', 'chat')

    const pinnedHeading = screen.getByText('Pinned chats')
    expect(pinnedHeading).toBeTruthy()
    expect(screen.getAllByText('Tile two')).toHaveLength(1)

    act(() => $pinnedSessionIds.set([]))

    expect(screen.queryByText('Pinned chats')).toBeNull()
    expect(screen.getByText('Tile two')).toBeTruthy()
  })

  it('files manually created project chats under Projects while keeping repo and pinned chats visible', () => {
    act(() => {
      $sessionsLoading.set(false)
      $projects.set([{
        archived: false, board_slug: null, color: null, created_at: 0, description: null,
        folders: [{ added_at: 0, is_primary: true, label: null, path: '/work/project' }],
        icon: null, id: 'p_project', name: 'Project', primary_path: '/work/project', slug: 'project'
      }])
      $sessions.set([
        makeSessionInfo({ id: 'manual', cwd: '/work/project', title: 'Manual chat' }),
        makeSessionInfo({ id: 'repo', cwd: '/work/repo', git_repo_root: '/work/repo', title: 'Repo chat' })
      ])
    })

    renderSidebar('/', 'chat', true)
    expect(screen.queryByText('Manual chat')).toBeNull()
    expect(screen.getByText('Repo chat')).toBeTruthy()

    act(() => $pinnedSessionIds.set(['manual']))
    expect(screen.getByText('Pinned chats')).toBeTruthy()
    expect(screen.getByText('Manual chat')).toBeTruthy()

    cleanup()
    act(() => $pinnedSessionIds.set([]))
    renderSidebar('/', 'chat')
    expect(screen.getByText('Manual chat')).toBeTruthy()
    expect(screen.getByText('Repo chat')).toBeTruthy()
  })

  // A bot's canonical Bot Chat is hidden by design and reachable only through
  // its bot row. Opening it caches the row in $sessions (for its own header and
  // tab), but the conversation list must never show it: as an untitled row
  // while it is focused, or once it has moved on.
  const botChatRow = (overrides: Parameters<typeof makeSessionInfo>[0] = {}) =>
    makeSessionInfo({
      hidden: 1,
      id: 'bot-chat',
      last_active: 3,
      profile: 'default',
      started_at: 3,
      title: 'Bot Chat',
      ...overrides
    })

  it('never lists the canonical Bot Chat while it is the focused chat', () => {
    act(() => {
      $sessionsLoading.set(false)
      $sessions.set([botChatRow(), ...sessionRows])
      $selectedStoredSessionId.set('bot-chat')
    })

    renderSidebar('/', 'chat')

    expect(screen.getByText('Tile one')).toBeTruthy()
    expect(screen.queryByText('Untitled session')).toBeNull()
    expect(screen.queryByText('Bot Chat')).toBeNull()
  })

  it('never lists the canonical Bot Chat once another chat is focused', () => {
    act(() => {
      $sessionsLoading.set(false)
      $sessions.set([botChatRow(), ...sessionRows])
      $selectedStoredSessionId.set('tile-one')
    })

    renderSidebar('/', 'chat')

    expect(screen.getByText('Tile one')).toBeTruthy()
    expect(screen.queryByText('Untitled session')).toBeNull()
    expect(screen.queryByText('Bot Chat')).toBeNull()
  })

  it('keeps the canonical Bot Chat out of Pinned even when its id is pinned', () => {
    act(() => {
      $sessionsLoading.set(false)
      $sessions.set([botChatRow(), ...sessionRows])
      $pinnedSessionIds.set(['bot-chat'])
    })

    renderSidebar('/', 'chat')

    expect(screen.queryByText('Pinned chats')).toBeNull()
    expect(screen.queryByText('Untitled session')).toBeNull()
  })

  it('still lists a hidden side-chat that keeps its own name', () => {
    // A `+` side-chat in Bot Mode is created hidden, but it is an ordinary
    // conversation with its own title, not the canonical registry row.
    act(() => {
      $sessionsLoading.set(false)
      $sessions.set([botChatRow({ id: 'side-chat', title: 'Side question' }), ...sessionRows])
      $selectedStoredSessionId.set('side-chat')
    })

    renderSidebar('/', 'chat')

    expect(screen.getByText('Side question')).toBeTruthy()
  })
})
