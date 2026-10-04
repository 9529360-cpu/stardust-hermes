// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { $layoutTree, noteActiveTreeGroup } from '@/components/pane-shell/tree/store'
import { I18nProvider } from '@/i18n'
import { $newChatProfile } from '@/store/profile'

import type { AppView } from '../routes'

import { PersonalProductNav } from './personal-product-nav'

afterEach(() => {
  cleanup()
  $newChatProfile.set(null)
  $layoutTree.set(null)
  noteActiveTreeGroup(null)
})

function renderNav(currentView: AppView, path = '/', onNavigate = vi.fn()) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <I18nProvider configClient={null} initialLocale="zh">
        <PersonalProductNav currentView={currentView} onNavigate={onNavigate} />
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
    expect(nav.className).toContain('pt-2.5')
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
