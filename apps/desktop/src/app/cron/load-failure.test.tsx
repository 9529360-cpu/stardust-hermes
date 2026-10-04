import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/api/client'
import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'
import {
  $cronJobs,
  beginCronJobsRequest,
  commitCronJobsRequest,
  invalidateCronJobsRequests,
  setCronJobs
} from '@/store/cron'
import { $activeGatewayProfile, $showAllProfiles } from '@/store/profile'

import { refreshCronJobs } from './cron-actions'

import { CronView } from './index'

const getCronJobs = vi.fn()
const { getCronJobRuns, pauseCronJob, triggerCronJob, deleteCronJob, updateCronJob } = vi.hoisted(() => ({
  getCronJobRuns: vi.fn(async () => []),
  pauseCronJob: vi.fn(),
  triggerCronJob: vi.fn(),
  deleteCronJob: vi.fn(),
  updateCronJob: vi.fn()
}))
const getCronSuggestions = vi.fn(async () => [] as unknown[])
const getAutomationBlueprints = vi.fn(async () => ({ blueprints: [] as unknown[] }))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getCronJobs: (...args: unknown[]) => getCronJobs(...args),
  getCronJobRuns,
  pauseCronJob,
  triggerCronJob,
  deleteCronJob,
  updateCronJob,
  getCronSuggestions: () => getCronSuggestions(),
  getAutomationBlueprints: () => getAutomationBlueprints()
}))

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <CronView />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  getCronJobs.mockReset()
  getCronJobRuns.mockClear()
  pauseCronJob.mockReset()
  triggerCronJob.mockReset()
  deleteCronJob.mockReset()
  updateCronJob.mockReset()
  getCronSuggestions.mockClear()
  getAutomationBlueprints.mockClear()
  $showAllProfiles.set(true)
  $activeGatewayProfile.set('default')
  setApiRequestProfile('default')
  setApiRequestConnection(null)
  setCronJobs([])
})

afterEach(() => {
  cleanup()
  invalidateCronJobsRequests()
  $showAllProfiles.set(false)
  setApiRequestProfile(null)
  setApiRequestConnection(null)
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
    commitCronJobsRequest(beginCronJobsRequest('\u0000all'), [
      { id: 'job-1', name: 'Morning summary', prompt: 'summary', schedule_display: 'daily' }
    ] as Parameters<typeof setCronJobs>[0])
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

  it('never exposes another profile’s cached jobs or actions when its own load fails', async () => {
    commitCronJobsRequest(beginCronJobsRequest('\u0000all'), [
      { id: 'foreign-job', name: 'Foreign summary', prompt: 'private', schedule_display: 'daily' }
    ] as Parameters<typeof setCronJobs>[0])
    getCronJobs.mockRejectedValue(new Error('offline'))
    mount()
    await screen.findByText(en.cron.loadFailedStale, { exact: false })
    expect(screen.getAllByText('Foreign summary')).toHaveLength(2)

    act(() => {
      $activeGatewayProfile.set('work')
      $showAllProfiles.set(false)
    })
    expect(await screen.findByText(en.cron.failedLoad)).toBeTruthy()
    expect(screen.queryByText('Foreign summary')).toBeNull()
    expect(screen.queryByText(en.cron.loadFailedStale, { exact: false })).toBeNull()
  })

  it('does not cancel a newer sidebar request when closing the overlay', async () => {
    let finishSidebar: (jobs: unknown[]) => void = () => {}
    getCronJobs
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockImplementationOnce(() => new Promise(resolve => { finishSidebar = resolve }))
    const view = mount()
    await waitFor(() => expect(getCronJobs).toHaveBeenCalledTimes(1))
    let sidebarRefresh: ReturnType<typeof refreshCronJobs>
    act(() => {
      sidebarRefresh = refreshCronJobs('all')
      view.unmount()
    })

    await act(async () => {
      finishSidebar([{ id: 'sidebar-job', name: 'Sidebar job' }])
      await sidebarRefresh
    })
    expect($cronJobs.get().map(job => job.id)).toEqual(['sidebar-job'])
  })

  it('routes an annotated aggregate job to its owner even when another profile is active', async () => {
    const job = { id: 'shared-id', profile: 'work', name: 'work owner', prompt: 'private', schedule_display: 'daily' }
    getCronJobs.mockResolvedValue([job])
    pauseCronJob.mockResolvedValue(job)
    mount()
    expect(await screen.findAllByText('work owner')).toHaveLength(2)

    const pause = screen.getByRole('button', { name: en.cron.pauseTitle })
    expect(pause).toHaveProperty('disabled', false)
    fireEvent.click(pause)
    await waitFor(() => expect(pauseCronJob).toHaveBeenCalledWith('shared-id', 'work'))
  })

  it('accepts an annotated backend owner for a concrete desktop profile alias', async () => {
    $showAllProfiles.set(false)
    $activeGatewayProfile.set('mara')
    setApiRequestProfile('mara')
    const job = { id: 'alias-id', profile: 'default', name: 'remote owner', schedule_display: 'daily' }
    getCronJobs.mockResolvedValue([job])
    pauseCronJob.mockResolvedValue(job)
    mount()
    expect(await screen.findAllByText('remote owner')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: en.cron.pauseTitle }))
    await waitFor(() => expect(pauseCronJob).toHaveBeenCalledWith('alias-id', 'default'))
  })

  it('keeps duplicate job IDs selectable by owning profile', async () => {
    getCronJobs.mockResolvedValue([
      { id: 'shared-id', profile: 'default', name: 'default copy', schedule_display: 'daily' },
      { id: 'shared-id', profile: 'work', name: 'work copy', schedule_display: 'daily' }
    ])
    mount()
    expect(await screen.findAllByText('default copy')).toHaveLength(2)
    fireEvent.click(screen.getByText('work copy'))
    expect(screen.getAllByText('work copy')).toHaveLength(2)
    expect(screen.getAllByText('default copy')).toHaveLength(1)
    await waitFor(() => expect(getCronJobRuns).toHaveBeenCalledWith('shared-id', 20, 'work'))
  })

  it('does not dispatch an ownerless aggregate row', async () => {
    getCronJobs.mockResolvedValue([{ id: 'shared-id', name: 'unknown owner', schedule_display: 'daily' }])
    mount()
    expect(await screen.findAllByText('unknown owner')).toHaveLength(2)
    expect(screen.getByRole('button', { name: en.cron.triggerNow })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: en.cron.pauseTitle })).toHaveProperty('disabled', true)
    expect(screen.queryByRole('button', { name: en.cron.manage })).toBeNull()
    expect(triggerCronJob).not.toHaveBeenCalled()
    expect(pauseCronJob).not.toHaveBeenCalled()
    expect(deleteCronJob).not.toHaveBeenCalled()
    expect(updateCronJob).not.toHaveBeenCalled()
  })

  it('checks the live route before dispatching a concrete-profile job action', async () => {
    $showAllProfiles.set(false)
    getCronJobs.mockResolvedValue([{ id: 'shared-id', name: 'owned job', schedule_display: 'daily' }])
    mount()
    expect(await screen.findAllByText('owned job')).toHaveLength(2)
    const trigger = screen.getByRole('button', { name: en.cron.triggerNow })
    expect(trigger).toHaveProperty('disabled', false)

    setApiRequestProfile('another-profile')
    fireEvent.click(trigger)
    expect(triggerCronJob).not.toHaveBeenCalled()

    setApiRequestProfile('default')
    setApiRequestConnection('other-connection')
    fireEvent.click(trigger)
    expect(triggerCronJob).not.toHaveBeenCalled()
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
