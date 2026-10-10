import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/api/client'
import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'
import { beginCronJobsRequest, commitCronJobsRequest, invalidateCronJobsRequests, setCronJobs } from '@/store/cron'
import { $activeGatewayProfile, $showAllProfiles } from '@/store/profile'

import { CronView } from './index'

const requestGateway = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

const JOBS = [
  { id: 'job-1', name: 'Morning summary', prompt: 'summarize my inbox', schedule_display: 'weekdays at 8am' },
  { id: 'job-2', name: 'Weekly report', prompt: 'write the report', schedule_display: 'every monday 9am' }
]

const { getCronJobRuns } = vi.hoisted(() => ({ getCronJobRuns: vi.fn(async () => []) }))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronJobs: vi.fn(async () => JOBS),
  getCronJobRuns,
  pauseCronJob: vi.fn(),
  triggerCronJob: vi.fn(),
  deleteCronJob: vi.fn(),
  updateCronJob: vi.fn(),
  getCronSuggestions: vi.fn(async () => []),
  getAutomationBlueprints: vi.fn(async () => ({ blueprints: [] }))
}))

const NOW = Math.floor(Date.now() / 1000)

const RUNS = [
  {
    id: 'cron:run-running',
    kind: 'cron',
    title: 'Cron job job-1',
    status: 'running',
    started_at: NOW - 60,
    updated_at: NOW - 60,
    detail: { job_id: 'job-1' }
  },
  {
    id: 'cron:run-failed',
    kind: 'cron',
    title: 'Cron job job-2',
    status: 'failed',
    started_at: NOW - 3600,
    updated_at: NOW - 3500,
    detail: { job_id: 'job-2', error: 'provider unavailable', delivery_outcome: 'failed' }
  }
]

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <CronView />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  requestGateway.mockReset()
  requestGateway.mockImplementation(async (method: string) =>
    method === 'cron.executions.list' ? { scoped: '', work: RUNS } : {}
  )
  $showAllProfiles.set(true)
  $activeGatewayProfile.set('default')
  setApiRequestProfile('default')
  setApiRequestConnection(null)
  setCronJobs([])
  commitCronJobsRequest(beginCronJobsRequest('\u0000all'), JOBS as Parameters<typeof setCronJobs>[0])
})

afterEach(() => {
  cleanup()
  invalidateCronJobsRequests()
  $showAllProfiles.set(false)
  setApiRequestProfile(null)
  setApiRequestConnection(null)
})

describe('cron page recent runs', () => {
  it('shows what is running now and what finished recently, each named after its job', async () => {
    mount()

    expect(await screen.findByText(en.cron.recentRuns.runningTitle)).toBeTruthy()
    expect(screen.getByText(en.cron.recentRuns.recentTitle)).toBeTruthy()
    expect(screen.getAllByText('Weekly report').length).toBeGreaterThan(0)
    expect(requestGateway).toHaveBeenCalledWith('cron.executions.list', { profile: '', limit: 30 })
  })

  it('explains a finished run and offers a way back to its job', async () => {
    mount()

    // Wait for the runs to land: until then only the job row exists under that name.
    await screen.findByText(en.cron.recentRuns.recentTitle)
    const weeklyRows = screen.getAllByText('Weekly report')

    // The job row comes first, the recent run row last.
    fireEvent.click(weeklyRows[weeklyRows.length - 1])

    expect(await screen.findByText('provider unavailable')).toBeTruthy()
    expect(screen.getByText(en.cron.recentRuns.status.failed)).toBeTruthy()
    expect(screen.getByText(en.cron.recentRuns.deliveryFailed)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: en.cron.recentRuns.openJob }))

    await waitFor(() => expect(screen.queryByText('provider unavailable')).toBeNull())
  })
})
