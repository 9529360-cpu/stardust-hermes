// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { PersonalProductNav } from './personal-product-nav'

afterEach(cleanup)

describe('PersonalProductNav', () => {
  it('renders only the compact Stardust primary navigation in product order', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <I18nProvider configClient={null} initialLocale="zh">
          <PersonalProductNav currentView="chat" onNavigate={vi.fn()} />
        </I18nProvider>
      </MemoryRouter>
    )

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
    const onNavigate = vi.fn()

    render(
      <MemoryRouter initialEntries={['/']}>
        <I18nProvider configClient={null} initialLocale="zh">
          <PersonalProductNav currentView="chat" onNavigate={onNavigate} />
        </I18nProvider>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: '新建对话' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ action: 'new-session', id: 'new-session' }))

    fireEvent.click(screen.getByRole('button', { name: '任务' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'cron', route: '/cron' }))

    fireEvent.click(screen.getByRole('button', { name: '工具' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'skills', route: '/skills?tab=toolsets' }))

    fireEvent.click(screen.getByRole('button', { name: '插件' }))
    expect(onNavigate).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'plugins', route: '/skills?tab=plugins' }))
  })
})
