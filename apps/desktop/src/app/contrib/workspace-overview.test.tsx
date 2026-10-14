// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import type * as Notifications from '@/store/notifications'
import type * as Prompts from '@/store/prompts'
import type { CronSuggestion } from '@/types/hermes'

const getCronSuggestions = vi.hoisted(() => vi.fn(async () => [] as CronSuggestion[]))
const openSession = vi.hoisted(() => vi.fn())
const submitApprovalResponse = vi.hoisted(() => vi.fn())
const notifyError = vi.hoisted(() => vi.fn())

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronSuggestions: () => getCronSuggestions()
}))

vi.mock('@/app/open-session', () => ({
  openSession: (...args: unknown[]) => openSession(...args)
}))

vi.mock('@/store/prompts', async importOriginal => {
  const actual = await importOriginal<typeof Prompts>()

  return { ...actual, submitApprovalResponse }
})

vi.mock('@/store/notifications', async importOriginal => {
  const actual = await importOriginal<typeof Notifications>()

  return { ...actual, notifyError }
})

import { I18nProvider } from '@/i18n'
import { $backgroundStatusBySession } from '@/store/composer-status'
import { setCronJobs } from '@/store/cron'
import { $gateway } from '@/store/gateway'
import { $projectTree } from '@/store/projects'
import { $approvalRequests, clearApprovalRequest, setApprovalRequest } from '@/store/prompts'
import { $currentCwd, $selectedStoredSessionId, $sessions } from '@/store/session'
import { $sessionStates } from '@/store/session-states'

import { WorkspaceOverview } from './workspace-overview'

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
  submitApprovalResponse.mockReset()
  notifyError.mockReset()
  clearApprovalRequest()
  $gateway.set(null)
})

afterEach(() => {
  cleanup()
  $currentCwd.set('')
  setCronJobs([])
  $selectedStoredSessionId.set(null)
  $sessionStates.set({})
  $sessions.set([])
  $projectTree.set([])
  $backgroundStatusBySession.set({})
  clearApprovalRequest()
  $gateway.set(null)
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

  it('clears the card after the shared approval response succeeds', async () => {
    const gateway = { request: vi.fn(async () => ({})) }

    const request = {
      command: 'delete generated files',
      description: 'Allow this command?',
      requestId: 'approval-1',
      serverRequestId: 'server-request-1',
      sessionId: 'runtime-session'
    }

    submitApprovalResponse.mockImplementationOnce(async (_gateway, submitted) => {
      clearApprovalRequest(submitted.sessionId, submitted.requestId)
    })
    $gateway.set(gateway as never)
    setApprovalRequest(request)
    renderOverview('en')

    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() => expect($approvalRequests.get()).toEqual({}))
    expect(submitApprovalResponse).toHaveBeenCalledWith(gateway, request, 'once')
    expect(screen.queryByRole('button', { name: 'Run' })).toBeNull()
  })

  it('reports an approval failure and leaves the request available for retry', async () => {
    const gateway = { request: vi.fn(async () => ({})) }

    const request = {
      command: 'delete generated files',
      description: 'Allow this command?',
      requestId: 'approval-retry',
      serverRequestId: 'server-request-retry',
      sessionId: 'runtime-session'
    }

    const failure = new Error('connection lost')
    submitApprovalResponse
      .mockRejectedValueOnce(failure)
      .mockImplementationOnce(async (_gateway, submitted) => {
        clearApprovalRequest(submitted.sessionId, submitted.requestId)
      })
    $gateway.set(gateway as never)
    setApprovalRequest(request)
    renderOverview('en')

    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() => expect(notifyError).toHaveBeenCalledWith(
      failure,
      'Could not submit the approval. Please try again.'
    ))
    expect($approvalRequests.get()).toEqual({ 'runtime-session': request })
    expect((screen.getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() => expect($approvalRequests.get()).toEqual({}))
    expect(submitApprovalResponse).toHaveBeenCalledTimes(2)
  })
})
