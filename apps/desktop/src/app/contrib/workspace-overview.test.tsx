// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $currentCwd } from '@/store/session'
import { $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'
import { $projectTree } from '@/store/projects'

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
  $sessionStates.set({})
  $sessions.set([])
  $projectTree.set([])
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

  it('shows workspace context in the current task result card', () => {
    $currentCwd.set('/work/app/.worktrees/feature')
    $sessionStates.set({ runtime: { storedSessionId: 'tip', needsInput: true } as never })
    $sessions.set([{
      id: 'tip', _lineage_root_id: 'root', title: 'Build feature', cwd: '/work/app/.worktrees/feature',
      git_branch: 'feature', git_repo_root: '/work/app', ended_at: null, is_active: true,
      last_active: 1, started_at: 1, input_tokens: 0, output_tokens: 0, message_count: 0, model: null
    } as never])
    $projectTree.set([{ id: 'app', label: 'App', path: '/work/app', repos: [], sessionCount: 0, previewSessions: [] } as never])
    renderOverview()
    expect(screen.getByText('/work/app/.worktrees/feature')).toBeTruthy()
  })
})
