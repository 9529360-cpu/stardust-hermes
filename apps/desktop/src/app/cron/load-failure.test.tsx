import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'
import { $cronJobs, invalidateCronJobsRequests, setCronJobs } from '@/store/cron'
import { $activeGatewayProfile, $showAllProfiles } from '@/store/profile'

import { CronView } from './index'

const getCronJobs = vi.fn()

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronJobs: (...args: unknown[]) => getCronJobs(...args),
  getCronSuggestions: async () => [],
  getAutomationBlueprints: async () => ({ blueprints: [] })
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
