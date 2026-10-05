// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $currentCwd } from '@/store/session'

import { WorkspaceOverview } from './workspace-overview'

function renderOverview() {
  render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="zh">
        <WorkspaceOverview />
      </I18nProvider>
    </MemoryRouter>
  )
}

afterEach(() => {
  cleanup()
  $currentCwd.set('')
})

describe('WorkspaceOverview (context rail)', () => {
  it('shows a quiet empty state, not project details, when nothing is in flight', () => {
    renderOverview()

    expect(screen.getByRole('complementary', { name: '上下文' })).toBeTruthy()
    expect(screen.getByText('无待处理项')).toBeTruthy()
    expect(screen.queryByText('项目信息')).toBeNull()
  })

  it('offers the workspace tools for a project without repeating its path or branch', () => {
    $currentCwd.set('D:/work/stardust-demo')
    renderOverview()

    for (const tool of ['文件', '审查', '终端']) {
      expect(screen.getByRole('button', { name: tool })).toBeTruthy()
    }

    expect(screen.queryByText('D:/work/stardust-demo')).toBeNull()
    expect(screen.queryByText('分支')).toBeNull()
    expect(screen.queryByText('无待处理项')).toBeNull()
  })
})
