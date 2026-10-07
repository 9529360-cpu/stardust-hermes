// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import type { CronSuggestion } from '@/types/hermes'

const getCronSuggestions = vi.hoisted(() => vi.fn(async () => [] as CronSuggestion[]))
const openSession = vi.hoisted(() => vi.fn())

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronSuggestions: () => getCronSuggestions()
}))

vi.mock('@/app/open-session', () => ({
  openSession: (...args: unknown[]) => openSession(...args)
}))

import { I18nProvider } from '@/i18n'
import { setCronJobs } from '@/store/cron'
import { $projectTree } from '@/store/projects'
import { $currentCwd, $selectedStoredSessionId, $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'

import { WorkspaceOverview } from './workspace-overview'

function renderOverview() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale="zh">
          <WorkspaceOverview />
        </I18nProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  getCronSuggestions.mockReset()
  getCronSuggestions.mockResolvedValue([])
  openSession.mockReset()
})

afterEach(() => {
  cleanup()
  $currentCwd.set('')
  setCronJobs([])
  $selectedStoredSessionId.set(null)
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

  it('shows pending cron suggestions in the Task Center', async () => {
    getCronSuggestions.mockResolvedValueOnce([{
      description: 'Review weekly notes',
      id: 'suggestion-1',
      job_spec: { schedule: '0 9 * * 1' },
      source: 'catalog',
      title: 'Review weekly notes'
    }])
    renderOverview()

    expect((await screen.findAllByText('Review weekly notes')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '查看' })).toBeTruthy()
  })

  it('opens a session beside the current work when the Task Center action is used', () => {
    $selectedStoredSessionId.set('other')
    $sessionStates.set({ runtime: { storedSessionId: 'tip', needsInput: true } as never })
    $sessions.set([{
      id: 'tip', _lineage_root_id: 'root', title: 'Build feature', ended_at: null, is_active: true,
      last_active: 1, started_at: 1, input_tokens: 0, output_tokens: 0, message_count: 0, model: null
    } as never])
    renderOverview()

    fireEvent.click(screen.getByRole('button', { name: '在旁边打开' }))

    expect(openSession).toHaveBeenCalledWith('tip', expect.any(Function), 'stack')
  })
})
