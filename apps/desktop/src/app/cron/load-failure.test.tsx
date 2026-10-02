import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'
import { $cronJobs, invalidateCronJobsRequests, setCronJobs } from '@/store/cron'
import { $activeGatewayProfile, $showAllProfiles } from '@/store/profile'

import { refreshCronJobs } from './cron-actions'

import { CronView } from './index'

const getCronJobs = vi.fn()
const getCronSuggestions = vi.fn(async () => [] as unknown[])
const getAutomationBlueprints = vi.fn(async () => ({ blueprints: [] as unknown[] }))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronJobs: (...args: unknown[]) => getCronJobs(...args),
  getCronSuggestions: () => getCronSuggestions(),
  getAutomationBlueprints: () => getAutomationBlueprints()
}))

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <CronView onClose={() => {}} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  getCronJobs.mockReset()
  getCronSuggestions.mockClear()
  getAutomationBlueprints.mockClear()
  $showAllProfiles.set(true)
  $activeGatewayProfile.set('default')
  setCronJobs([])
})

afterEach(() => {
  cleanup()
  invalidateCronJobsRequests()
  $showAllProfiles.set(false)
})

describe('cron list load failure', () => {
  it('does not call an unknown list empty; retry recovers and removes the error', async () => {
    getCronJobs.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
    mount()

    const retry = await screen.findByRole('button', { name: en.common.retry })
    expect(screen.getByText(en.cron.failedLoad)).toBeTruthy()
    expect(screen.queryByText(en.cron.emptyTitleNew)).toBeNull()
    fireEvent.click(retry)

    await waitFor(() => expect(screen.getByText(en.cron.emptyTitleNew)).toBeTruthy())
    expect(screen.queryByText(en.cron.failedLoad)).toBeNull()
    expect(getCronJobs).toHaveBeenCalledTimes(2)
  })

  it('keeps the cached jobs visible and labels them stale when refresh fails', async () => {
    setCronJobs([{ id: 'job-1', name: 'Morning summary', prompt: 'summary', schedule_display: 'daily' }] as Parameters<
      typeof setCronJobs
    >[0])
    getCronJobs.mockRejectedValue(new Error('offline'))
    mount()

    await screen.findByText(en.cron.loadFailedStale, { exact: false })
    expect(screen.getAllByText('Morning summary')).toHaveLength(2)
    expect($cronJobs.get()).toHaveLength(1)
    expect(screen.queryByText(en.cron.emptyTitleNew)).toBeNull()
  })

  it('settles an overlay request superseded by a failing sidebar refresh', async () => {
    let rejectOverlay: (error: Error) => void = () => {}
    getCronJobs
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectOverlay = reject }))
      .mockRejectedValueOnce(new Error('sidebar offline'))
    mount()
    await waitFor(() => expect(getCronJobs).toHaveBeenCalledTimes(1))

    await act(async () => {
      await refreshCronJobs('all')
      rejectOverlay(new Error('superseded'))
    })
    expect(await screen.findByText(en.cron.failedLoad)).toBeTruthy()
    expect(screen.queryByText(en.cron.loading)).toBeNull()
  })

  it('clears the error after an independent sidebar refresh loads an empty list', async () => {
    getCronJobs.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
    mount()
    await screen.findByText(en.cron.failedLoad)

    await act(async () => { await refreshCronJobs('all') })
    expect(await screen.findByText(en.cron.emptyTitleNew)).toBeTruthy()
    expect(screen.queryByText(en.cron.failedLoad)).toBeNull()
  })

  it('keeps independently loaded suggestions available when the jobs list fails', async () => {
    getCronSuggestions.mockResolvedValueOnce([{ id: 'suggestion-1', title: 'Review weekly notes', description: 'Weekly review', job_spec: { schedule: '0 9 * * 1' } }])
    getCronJobs.mockRejectedValue(new Error('offline'))
    mount()

    await screen.findByText(en.cron.loadFailedHelp, { exact: false })
    expect(getCronSuggestions).toHaveBeenCalled()
    expect(screen.getAllByText('Review weekly notes').length).toBeGreaterThan(0)
    expect(screen.queryByText(en.cron.emptyTitleNew)).toBeNull()
  })

  it('does not show the prior profile error when switching profile', async () => {
    getCronJobs.mockRejectedValueOnce(new Error('offline')).mockImplementation(() => new Promise(() => {}))
    mount()
    await screen.findByText(en.cron.failedLoad)

    act(() => {
      $activeGatewayProfile.set('work')
      $showAllProfiles.set(false)
    })
    await waitFor(() => expect(getCronJobs).toHaveBeenCalledWith('work'))
    expect(screen.queryByText(en.cron.failedLoad)).toBeNull()
  })
})
