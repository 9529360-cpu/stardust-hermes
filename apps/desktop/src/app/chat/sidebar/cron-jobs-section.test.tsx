import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, TRANSLATIONS } from '@/i18n'
import { fmtDayTime } from '@/lib/time'
import { $confirmRequest, settleConfirm } from '@/store/confirm'
import { $cronJobs, $cronJobsScope, beginCronJobsRequest, commitCronJobsRequest, setCronJobs } from '@/store/cron'
import { $activeGatewayProfile, $showAllProfiles } from '@/store/profile'
import type { CronJob, SessionInfo } from '@/types/hermes'

import { SidebarCronJobsSection } from './cron-jobs-section'

const { deleteCronJob, getCronJobRuns, pauseCronJob, route } = vi.hoisted(() => ({
  deleteCronJob: vi.fn(),
  getCronJobRuns: vi.fn(),
  pauseCronJob: vi.fn(),
  route: { connection: null as null | string, profile: 'work' }
}))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<object>()),
  deleteCronJob,
  getApiRequestConnection: () => route.connection,
  getApiRequestProfile: () => route.profile,
  getCronJobRuns,
  pauseCronJob
}))

const RUN = {
  connection_id: 'gw-tailscale',
  ended_at: null,
  id: 'cron-nightly-1',
  input_tokens: 0,
  is_active: false,
  last_active: 1_700_000_000,
  message_count: 2,
  model: null,
  output_tokens: 0,
  profile: 'research',
  source: 'cron',
  started_at: 1_699_999_900,
  title: 'nightly run',
  tool_call_count: 0
} as SessionInfo

const JOB = {
  enabled: true,
  id: 'job-1',
  name: 'nightly',
  schedule: '* * * * *',
  state: 'scheduled'
} as CronJob

beforeEach(() => {
  route.connection = null
  route.profile = 'work'
  $showAllProfiles.set(false)
  $activeGatewayProfile.set('work')
  setCronJobs([])
  getCronJobRuns.mockResolvedValue([RUN])
})

afterEach(() => {
  cleanup()
  settleConfirm(false)
  $activeGatewayProfile.set('default')
  $showAllProfiles.set(false)
  setCronJobs([])
  getCronJobRuns.mockReset()
  pauseCronJob.mockReset()
  deleteCronJob.mockReset()
})

function renderSection(onOpenRun: (sessionId: string, session?: SessionInfo) => void, jobsScope = '\u0000all') {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <SidebarCronJobsSection
        jobs={[JOB]}
        jobsScope={jobsScope}
        label="Cron jobs"
        onManageJob={() => {}}
        onOpenRun={onOpenRun}
        onToggle={() => {}}
        onTriggerJob={async () => {}}
        open
      />
    </I18nProvider>
  )
}

describe('SidebarCronJobsSection action ownership', () => {
  it('refuses ambient mutations from an all-profiles list without row ownership', async () => {
    $showAllProfiles.set(true)
    const scope = '\u0000all'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    expect(screen.queryByRole('menuitem', { name: TRANSLATIONS.en.cron.triggerNow })).toBeNull()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: TRANSLATIONS.en.cron.triggerNow, hidden: true }).disabled).toBe(true)
    expect(screen.queryByRole('menuitem', { name: TRANSLATIONS.en.cron.pause })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: TRANSLATIONS.en.common.delete })).toBeNull()
    expect(pauseCronJob).not.toHaveBeenCalled()
    expect(deleteCronJob).not.toHaveBeenCalled()
  })

  it('does not overwrite another profile after a pause returns late', async () => {
    const scope = '\u0000work'
    const request = beginCronJobsRequest(scope)
    commitCronJobsRequest(request, [JOB])

    let complete!: (job: CronJob) => void
    pauseCronJob.mockImplementation(() => new Promise<CronJob>(resolve => { complete = resolve }))
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    expect(pauseCronJob).toHaveBeenCalledWith(JOB.id)

    const nextJob = { ...JOB, name: 'other profile' }
    const next = beginCronJobsRequest('\u0000personal')
    act(() => { commitCronJobsRequest(next, [nextJob]) })
    await act(async () => { complete({ ...JOB, state: 'paused' }) })

    expect($cronJobsScope.get()).toBe('\u0000personal')
    expect($cronJobs.get()).toEqual([nextJob])
  })

  it('does not delete a job after confirmation switches profile', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))
    expect($confirmRequest.get()?.title).toBe(TRANSLATIONS.en.cron.deleteTitle)

    const next = beginCronJobsRequest('\u0000personal')
    act(() => {
      commitCronJobsRequest(next, [{ ...JOB, name: 'other profile' }])
      settleConfirm(true)
    })
    await act(async () => { await Promise.resolve() })

    expect(deleteCronJob).not.toHaveBeenCalled()
    expect($cronJobsScope.get()).toBe('\u0000personal')
  })

  it('refuses to delete during the incoming profile read before its cache commits', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))

    route.profile = 'personal'
    const pending = beginCronJobsRequest('\u0000personal')
    await act(async () => { settleConfirm(true) })

    expect(deleteCronJob).not.toHaveBeenCalled()
    expect(commitCronJobsRequest(pending, [{ ...JOB, name: 'personal job' }])).toBe(true)
    expect($cronJobs.get()[0]?.name).toBe('personal job')
  })

  it('refuses to pause an outgoing row while the new connection read is pending', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    route.connection = 'other-gateway'
    const pending = beginCronJobsRequest('other-gateway\u0000work')

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))

    expect(pauseCronJob).not.toHaveBeenCalled()
    expect(commitCronJobsRequest(pending, [JOB])).toBe(true)
  })

  it('rejects confirmation after leaving and returning to the same profile', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))

    route.profile = 'personal'
    beginCronJobsRequest('\u0000personal')
    route.profile = 'work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    await act(async () => { settleConfirm(true) })

    expect(deleteCronJob).not.toHaveBeenCalled()
  })

  it('does not remove another profile\'s matching job after deletion returns late', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])

    let complete!: (result: { ok: boolean }) => void
    deleteCronJob.mockImplementation(() => new Promise<{ ok: boolean }>(resolve => { complete = resolve }))
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))
    await act(async () => { settleConfirm(true) })
    expect(deleteCronJob).toHaveBeenCalledWith(JOB.id)

    const other = { ...JOB, name: 'other profile' }
    act(() => { commitCronJobsRequest(beginCronJobsRequest('\u0000personal'), [other]) })
    await act(async () => { complete({ ok: true }) })

    expect($cronJobsScope.get()).toBe('\u0000personal')
    expect($cronJobs.get()).toEqual([other])
  })

  it('does not cancel a newer pending same-profile read after a pause returns late', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    let complete!: (job: CronJob) => void
    pauseCronJob.mockImplementation(() => new Promise<CronJob>(resolve => { complete = resolve }))
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    const newer = { ...JOB, name: 'authoritative pending refresh' }
    const pending = beginCronJobsRequest(scope)
    await act(async () => { complete({ ...JOB, state: 'paused' }) })

    expect(commitCronJobsRequest(pending, [newer])).toBe(true)
    expect($cronJobs.get()).toEqual([newer])
  })

  it('does not discard a newer same-profile refresh after a pause returns late', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])

    let complete!: (job: CronJob) => void
    pauseCronJob.mockImplementation(() => new Promise<CronJob>(resolve => { complete = resolve }))
    renderSection(vi.fn(), scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    const newer = { ...JOB, name: 'updated elsewhere' }
    act(() => { commitCronJobsRequest(beginCronJobsRequest(scope), [newer]) })
    await act(async () => { complete({ ...JOB, state: 'paused' }) })

    expect($cronJobs.get()).toEqual([newer])
  })
})

describe('SidebarCronJobsSection run rows', () => {
  it('passes the full run row to the owner-aware open callback', async () => {
    const onOpenRun = vi.fn()
    renderSection(onOpenRun)

    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.showRuns }))

    const runButton = await screen.findByRole('button', {
      name: fmtDayTime.format(new Date(RUN.last_active * 1000))
    })

    fireEvent.click(runButton)

    expect(onOpenRun).toHaveBeenCalledWith(RUN.id, RUN)
  })
})
