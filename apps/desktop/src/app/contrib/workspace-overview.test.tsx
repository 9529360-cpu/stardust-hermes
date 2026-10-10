// @vitest-environment jsdom
import type { WorkItem } from '@hermes/shared'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import type { CronSuggestion } from '@/types/hermes'

const getCronSuggestions = vi.hoisted(() => vi.fn(async () => [] as CronSuggestion[]))
const openSession = vi.hoisted(() => vi.fn())
const requestGateway = vi.hoisted(() => vi.fn(async () => ({ work: [] as WorkItem[] })))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronSuggestions: () => getCronSuggestions()
}))

vi.mock('@/app/open-session', () => ({
  openSession: (...args: unknown[]) => openSession(...args)
}))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

import { I18nProvider } from '@/i18n'
import { $backgroundStatusBySession } from '@/store/composer-status'
import { setCronJobs } from '@/store/cron'
import { $gateway } from '@/store/gateway'
import { $projectTree } from '@/store/projects'
import { $activeSessionId, $currentCwd, $selectedStoredSessionId, $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'

import { WorkLedgerSection, WorkspaceOverview } from './workspace-overview'

const englishWorkLabels = {
  cancel: 'Stop work',
  empty: 'No work',
  kinds: { cron: 'Scheduled run', delegation: 'Delegation', process: 'Background process', subagent: 'Subagent' },
  statuses: { cancelled: 'Cancelled', completed: 'Completed', failed: 'Failed', interrupted: 'Interrupted', running: 'Running' },
  title: 'Background work'
} as const

function renderOverview(initialLocale: 'en' | 'zh' = 'zh') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale={initialLocale}>
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
  requestGateway.mockReset()
  requestGateway.mockResolvedValue({ work: [] })
})

afterEach(() => {
  cleanup()
  $currentCwd.set('')
  setCronJobs([])
  $activeSessionId.set(null)
  $gateway.set(null)
  $selectedStoredSessionId.set(null)
  $sessionStates.set({})
  $sessions.set([])
  $projectTree.set([])
  $backgroundStatusBySession.set({})
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

  it('shows owned work ledger items and cancels a running item', async () => {
    const item: WorkItem = {
      id: 'subagent:child-1',
      kind: 'subagent',
      title: 'Research task',
      status: 'running',
      started_at: 100,
      updated_at: 101,
      detail: {}
    }

    requestGateway.mockResolvedValueOnce({ work: [item] }).mockResolvedValueOnce({ work: [] })
    $activeSessionId.set('runtime-1')
    $gateway.set({ connectionState: 'open' } as never)
    renderOverview('en')

    expect(await screen.findByText('Research task')).toBeTruthy()
    expect(screen.getByText('Subagent · Running')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Stop work: Research task' }))

    expect(requestGateway).toHaveBeenLastCalledWith('work.cancel', {
      id: item.id,
      session_id: 'runtime-1'
    })
  })

  it('clears work from the previous session before the new scope resolves', async () => {
    let resolveDeferred: ((value: { work: WorkItem[] }) => void) | undefined

    const deferred = new Promise<{ work: WorkItem[] }>(resolve => {
      resolveDeferred = resolve
    })

    const oldItem: WorkItem = {
      id: 'subagent:old',
      kind: 'subagent',
      title: 'Old session work',
      status: 'running',
      started_at: 100,
      updated_at: 101,
      detail: {}
    }

    const newItem: WorkItem = {
      id: 'subagent:new',
      kind: 'subagent',
      title: 'New session work',
      status: 'running',
      started_at: 200,
      updated_at: 201,
      detail: {}
    }

    requestGateway.mockResolvedValueOnce({ work: [oldItem] }).mockImplementationOnce(() => deferred)
    $activeSessionId.set('session-a')
    $gateway.set({ connectionState: 'open' } as never)
    renderOverview('en')
    expect(await screen.findByText('Old session work')).toBeTruthy()

    await act(async () => {
      $activeSessionId.set('session-b')
    })

    expect(screen.queryByText('Old session work')).toBeNull()
    resolveDeferred?.({ work: [newItem] })
    expect(await screen.findByText('New session work')).toBeTruthy()
  })

  it('keeps polling so work that starts after the first load appears without a session switch', async () => {
    const item: WorkItem = {
      id: 'subagent:late',
      kind: 'subagent',
      title: 'Late subagent',
      status: 'running',
      started_at: 300,
      updated_at: 301,
      detail: {}
    }

    vi.useFakeTimers()

    try {
      requestGateway.mockResolvedValueOnce({ work: [] }).mockResolvedValueOnce({ work: [item] })
      $activeSessionId.set('session-poll')
      $gateway.set({ connectionState: 'open' } as never)
      renderOverview('en')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })

      expect(screen.getByText('Late subagent')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps work visible when the stop request is refused', async () => {
    const item: WorkItem = {
      id: 'subagent:refused',
      kind: 'subagent',
      title: 'Research task',
      status: 'running',
      started_at: 100,
      updated_at: 101,
      detail: {}
    }

    requestGateway
      .mockResolvedValueOnce({ work: [item] })
      .mockResolvedValueOnce({
        id: item.id,
        message: 'Work is no longer controllable by this process.',
        status: 'unavailable'
      } as never)
    $activeSessionId.set('runtime-refused')
    $gateway.set({ connectionState: 'open' } as never)
    renderOverview('en')
    expect(await screen.findByText('Research task')).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Stop work: Research task' }))
    })

    expect(requestGateway).toHaveBeenLastCalledWith('work.cancel', { id: item.id, session_id: 'runtime-refused' })
    expect(screen.getByText('Research task')).toBeTruthy()
  })

  it('hides stop controls for non-cancellable work kinds', () => {
    const process: WorkItem = {
      id: 'process:one',
      kind: 'process',
      title: 'Background process',
      status: 'running',
      started_at: 100,
      updated_at: 101,
      detail: {}
    }

    const cron: WorkItem = {
      id: 'cron:exec-1',
      kind: 'cron',
      title: 'Inbox digest',
      status: 'running',
      started_at: 102,
      updated_at: 103,
      detail: {}
    }

    renderOverview('en')

    render(<WorkLedgerSection items={[process, cron]} labels={englishWorkLabels} onCancel={vi.fn()} />)

    expect(screen.getByText('Background process')).toBeTruthy()
    expect(screen.getByText('Scheduled run · Running')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Stop work: Background process' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Stop work: Inbox digest' })).toBeNull()
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

  it('filters the Task Center to failed tests while keeping the existing task cards', () => {
    $backgroundStatusBySession.set({
      runtime: [
        { id: 'failed', state: 'failed', title: 'pytest failed', type: 'background', exitCode: 1 },
        { id: 'passed', state: 'done', title: 'pytest passed', type: 'background', exitCode: 0 }
      ]
    })
    renderOverview('en')

    expect(screen.queryAllByText('pytest failed').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('pytest passed').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Needs attention' }))

    expect(screen.getByText('Review queue')).toBeTruthy()
    expect(screen.queryAllByText('pytest failed').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('pytest passed')).toHaveLength(0)
  })

  it('drills into a Task Center item without losing its existing action', () => {
    $backgroundStatusBySession.set({
      runtime: [{ id: 'failed', state: 'failed', title: 'pytest failed', type: 'background', exitCode: 1 }]
    })
    renderOverview('en')

    fireEvent.click(screen.getByRole('button', { name: 'pytest failed' }))

    expect(screen.getByTestId('task-center-detail')).toBeTruthy()
    expect(screen.getByText('Task details')).toBeTruthy()
    expect(screen.getByText('Process-local')).toBeTruthy()
  })
  it('opens a session beside the current work when the Task Center action is used', () => {
    $selectedStoredSessionId.set('other')
    $sessionStates.set({ runtime: { storedSessionId: 'tip', needsInput: true } as never })
    $sessions.set([{
      id: 'tip', _lineage_root_id: 'root', title: 'Build feature', ended_at: null, is_active: true,
      last_active: 1, started_at: 1, input_tokens: 0, output_tokens: 0, message_count: 0, model: null
    } as never])
    renderOverview()

    fireEvent.click(screen.getByRole('button', { name: '需要关注' }))
    fireEvent.click(screen.getByRole('button', { name: '在旁边打开' }))

    expect(openSession).toHaveBeenCalledWith('tip', expect.any(Function), 'stack')
  })
})
