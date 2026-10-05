// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree, noteActiveTreeGroup } from '@/components/pane-shell/tree/store'
import { I18nProvider } from '@/i18n'
import { $sidebarGrouping, setSidebarAgentsGrouped } from '@/store/layout'
import { $newChatProfile } from '@/store/profile'
import { $projectScope, $projectTree, ALL_PROJECTS, deleteProject, fetchProjectSessions } from '@/store/projects'
import { $currentCwd } from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

import type { AppView } from '../routes'

import { PersonalProductNav } from './personal-product-nav'

vi.mock('@/store/projects', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  deleteProject: vi.fn().mockResolvedValue(undefined),
  fetchProjectSessions: vi.fn().mockResolvedValue(null)
}))

afterEach(() => {
  cleanup()
  vi.mocked(fetchProjectSessions).mockResolvedValue(null)
  $newChatProfile.set(null)
  $projectTree.set([])
  $projectScope.set(ALL_PROJECTS)
  $currentCwd.set('')
  setSidebarAgentsGrouped(false)
  $layoutTree.set(null)
  noteActiveTreeGroup(null)
})

function renderNav(currentView: AppView, path = '/', onNavigate = vi.fn(), onResumeSession = vi.fn()) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <I18nProvider configClient={null} initialLocale="zh">
        <PersonalProductNav currentView={currentView} onNavigate={onNavigate} onResumeSession={onResumeSession} />
      </I18nProvider>
    </MemoryRouter>
  )

  return onNavigate
}

const currentButtons = () =>
  screen
    .getAllByRole('button')
    .filter(button => button.getAttribute('aria-current') === 'page')
    .map(button => button.textContent?.trim())

describe('PersonalProductNav', () => {
  it('renders only the compact Stardust primary navigation in product order', () => {
    renderNav('chat')

    const nav = screen.getByRole('navigation', { name: 'Product navigation' })
    expect(nav.className).toContain('pt-0')
    expect(nav.className).not.toContain('titlebar-height')

    expect(screen.getAllByRole('button').map(button => button.textContent?.trim())).toEqual([
      '新建对话',
      '任务',
      '工具',
      '插件',
      '项目'
    ])
    expect(screen.queryByRole('button', { name: '对话' })).toBeNull()
    expect(screen.queryByRole('button', { name: '知识库' })).toBeNull()
    expect(screen.queryByRole('button', { name: '设置' })).toBeNull()
  })

  it('routes new chat, tasks, tools, and plugins through their existing owners', () => {
    const onNavigate = renderNav('chat')

    fireEvent.click(screen.getByRole('button', { name: '新建对话' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ action: 'new-session', id: 'new-session' }))

    fireEvent.click(screen.getByRole('button', { name: '任务' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'cron', route: '/cron' }))

    fireEvent.click(screen.getByRole('button', { name: '工具' }))
    expect(onNavigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'skills', route: '/skills?tab=toolsets' })
    )

    fireEvent.click(screen.getByRole('button', { name: '插件' }))
    expect(onNavigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'plugins', route: '/skills?tab=plugins' })
    )
  })

  it('starts a plain new chat in the live profile, not a stale per-profile pick', () => {
    // Same contract as the `session.new` keybind: a quick-create into another
    // profile (profile rail, `/profile`) must not leak into the next plain chat.
    $newChatProfile.set('work')

    const onNavigate = renderNav('chat')

    fireEvent.click(screen.getByRole('button', { name: '新建对话' }))

    expect($newChatProfile.get()).toBeNull()
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('offers the project actions on right-click, so a project can be deleted (Home has none)', async () => {
    $projectTree.set([
      { id: 'no-project', isNoProject: true, label: 'Home', path: null, repos: [], sessionCount: 3 },
      { id: 'p_example', label: 'Example', path: 'D:/Example', repos: [], sessionCount: 2 }
    ])
    renderNav('chat')
    fireEvent.click(screen.getByRole('button', { name: '项目' }))

    fireEvent.contextMenu(screen.getByRole('button', { name: 'Home' }))
    expect(screen.queryByRole('menuitem')).toBeNull()

    fireEvent.contextMenu(screen.getByRole('button', { name: 'Example' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: '删除…' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '删除' }))

    await act(async () => {})
    expect(deleteProject).toHaveBeenCalledWith('p_example')
  })

  it('expands projects beneath the nav without replacing recent conversations', () => {
    $projectTree.set([{ id: 'p_example', label: 'Example', path: 'D:/Example', repos: [], sessionCount: 2 }])
    setSidebarAgentsGrouped(true) // persisted legacy view must return to recents
    const onNavigate = renderNav('chat')
    const projectButton = screen.getByRole('button', { name: '项目' })

    fireEvent.click(projectButton)
    expect(projectButton.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('button', { name: 'Example' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '新建项目' })).toBeTruthy()
    expect($sidebarGrouping.get()).not.toBe('project')
    expect(onNavigate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Example' }))
    expect($projectScope.get()).toBe('p_example')
    expect($currentCwd.get()).toBe('D:/Example')
    expect($sidebarGrouping.get()).not.toBe('project')
    expect(onNavigate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Example' }))
    expect(screen.getByRole('button', { name: 'Example' }).getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(projectButton)
    expect(projectButton.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: 'Example' })).toBeNull()
  })

  it('opens a project conversation from its nested list without removing recents', async () => {
    const session = { id: 'chat-1', title: '项目讨论', preview: null } as SessionInfo

    const project = {
      id: 'p_example',
      label: 'Example',
      path: 'D:/Example',
      sessionCount: 1,
      repos: [
        {
          id: 'repo',
          label: 'Repo',
          path: 'D:/Example',
          sessionCount: 1,
          groups: [{ id: 'lane', label: 'main', path: 'D:/Example', sessions: [session] }]
        }
      ]
    }

    $projectTree.set([project])
    vi.mocked(fetchProjectSessions).mockResolvedValue(project)
    const onResumeSession = vi.fn()
    renderNav('chat', '/', vi.fn(), onResumeSession)

    fireEvent.click(screen.getByRole('button', { name: '项目' }))
    fireEvent.click(screen.getByRole('button', { name: 'Example' }))
    const conversation = await screen.findByRole('button', { name: '项目讨论' })
    fireEvent.click(conversation)
    expect(onResumeSession).toHaveBeenCalledWith('chat-1', session)
    expect($sidebarGrouping.get()).not.toBe('project')
  })

  it('marks exactly the page that owns the current route', () => {
    renderNav('cron', '/cron')
    expect(currentButtons()).toEqual(['任务'])
    cleanup()

    renderNav('skills', '/skills?tab=toolsets')
    expect(currentButtons()).toEqual(['工具'])
    cleanup()

    renderNav('skills', '/skills?tab=mcp')
    expect(currentButtons()).toEqual(['工具'])
    cleanup()

    renderNav('skills', '/skills?tab=plugins')
    expect(currentButtons()).toEqual(['插件'])
    cleanup()

    renderNav('chat')
    expect(currentButtons()).toEqual([])
  })

  it('drops the page highlight while a session tile owns focus', () => {
    $layoutTree.set(
      split('row', [
        group(['workspace'], { active: 'workspace', id: 'workspace-group' }),
        group(['session-tile:tile-one'], { active: 'session-tile:tile-one', id: 'tile-one-group' })
      ])
    )
    noteActiveTreeGroup('workspace-group')

    renderNav('cron', '/cron')
    expect(currentButtons()).toEqual(['任务'])

    act(() => noteActiveTreeGroup('tile-one-group'))
    expect(currentButtons()).toEqual([])

    act(() => noteActiveTreeGroup('workspace-group'))
    expect(currentButtons()).toEqual(['任务'])
  })
})
