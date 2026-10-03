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

function renderSection(
  onOpenRun: (sessionId: string, session?: SessionInfo) => void,
  jobsScope = '\u0000all',
  jobs: CronJob[] = [JOB],
  onTriggerJob: (jobId: string, owner?: string) => Promise<void> = async () => {},
  onManageJob: (jobId: string, owner?: string) => void = () => {}
) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <SidebarCronJobsSection
        jobs={jobs}
        jobsScope={jobsScope}
        label="Cron jobs"
        onManageJob={onManageJob}
        onOpenRun={onOpenRun}
        onToggle={() => {}}
        onTriggerJob={onTriggerJob}
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

  it('refuses stale same-scope mutations while a newer read is pending', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    const onTrigger = vi.fn().mockResolvedValue(undefined)
    const onManage = vi.fn()
    renderSection(vi.fn(), scope, [JOB], onTrigger, onManage)
    const pending = beginCronJobsRequest(scope)

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.triggerNow, hidden: true }))
    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.manage, hidden: true }))

    expect(pauseCronJob).not.toHaveBeenCalled()
    expect(onTrigger).not.toHaveBeenCalled()
    expect(onManage).not.toHaveBeenCalled()
    expect(commitCronJobsRequest(pending, [JOB])).toBe(true)
  })

  it('routes a concrete local alias to the annotated backend profile', async () => {
    $activeGatewayProfile.set('mara')
    route.profile = 'mara'
    const scope = '\u0000mara'
    const backendJob = { ...JOB, profile: 'default' }
    commitCronJobsRequest(beginCronJobsRequest(scope), [backendJob])
    pauseCronJob.mockResolvedValue({ ...backendJob, state: 'paused' })
    const onTrigger = vi.fn().mockResolvedValue(undefined)
    const onManage = vi.fn()
    renderSection(vi.fn(), scope, [backendJob], onTrigger, onManage)

    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.showRuns }))
    expect(getCronJobRuns).toHaveBeenCalledWith(JOB.id, 5, 'default')
    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.triggerNow, hidden: true }))
    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.manage, hidden: true }))
    expect(onTrigger).toHaveBeenCalledWith(JOB.id, 'default')
    expect(onManage).toHaveBeenCalledWith(JOB.id, 'default')

    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    await act(async () => { await Promise.resolve() })
    expect(pauseCronJob).toHaveBeenCalledWith(JOB.id, 'default')
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
    expect(pauseCronJob).toHaveBeenCalledWith(JOB.id, 'work')

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

  it('rejects confirmation after a same-profile read replaces the row', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))

    const replacement = { ...JOB, name: 'new owner of reused id' }
    act(() => { commitCronJobsRequest(beginCronJobsRequest(scope), [replacement]) })
    await act(async () => { settleConfirm(true) })

    expect(deleteCronJob).not.toHaveBeenCalled()
    expect($cronJobs.get()).toEqual([replacement])
  })

  it('rejects confirmation while a newer same-profile read is pending', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    fireEvent.contextMenu(screen.getByText('nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))

    const pending = beginCronJobsRequest(scope)
    await act(async () => { settleConfirm(true) })

    expect(deleteCronJob).not.toHaveBeenCalled()
    expect(commitCronJobsRequest(pending, [{ ...JOB, name: 'newer read' }])).toBe(true)
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
    expect(deleteCronJob).toHaveBeenCalledWith(JOB.id, 'work')

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

describe('SidebarCronJobsSection aggregate ownership', () => {
  const scope = '\u0000all'
  const work = { ...JOB, name: 'work nightly', profile: 'work' }
  const research = { ...JOB, name: 'research nightly', profile: 'research' }

  beforeEach(() => {
    $showAllProfiles.set(true)
    commitCronJobsRequest(beginCronJobsRequest(scope), [work, research])
  })

  it('keeps duplicate IDs independent and routes mutations by annotated owner', async () => {
    const onTrigger = vi.fn().mockResolvedValue(undefined)
    pauseCronJob.mockResolvedValue({ ...research, state: 'paused' })
    renderSection(vi.fn(), scope, [work, research], onTrigger)

    fireEvent.click(screen.getByText('research nightly'))
    expect(getCronJobRuns).toHaveBeenCalledWith(JOB.id, 5, 'research')
    fireEvent.contextMenu(screen.getByText('research nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.pause }))
    await act(async () => { await Promise.resolve() })
    expect(pauseCronJob).toHaveBeenCalledWith(JOB.id, 'research')
    expect($cronJobs.get()).toEqual([work, { ...research, state: 'paused' }])

    fireEvent.contextMenu(screen.getByText('work nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.cron.triggerNow }))
    await act(async () => { await Promise.resolve() })
    expect(onTrigger).toHaveBeenCalledWith(JOB.id, 'work')
  })

  it('passes the owner to manage and suppresses ownerless aggregate management', () => {
    const onManage = vi.fn()
    renderSection(vi.fn(), scope, [work, research, JOB], async () => {}, onManage)
    fireEvent.contextMenu(screen.getByText('research nightly'))
    fireEvent.click(screen.getByRole('menuitem', { name: TRANSLATIONS.en.cron.manage }))
    expect(onManage).toHaveBeenCalledWith(JOB.id, 'research')

    fireEvent.click(screen.getByText('nightly', { exact: true }))
    expect(screen.getAllByRole<HTMLButtonElement>('button', { name: TRANSLATIONS.en.cron.manage, hidden: true }).find(button => button.disabled)).toBeDefined()
    fireEvent.click(screen.getAllByRole<HTMLButtonElement>('button', { name: TRANSLATIONS.en.cron.manage, hidden: true }).find(button => button.disabled)!)
    expect(onManage).toHaveBeenCalledTimes(1)
  })

  it('deletes only the confirmed owner when IDs overlap', async () => {
    deleteCronJob.mockResolvedValue({ ok: true })
    renderSection(vi.fn(), scope, [work, research])
    fireEvent.contextMenu(screen.getByText('research nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))
    await act(async () => { settleConfirm(true) })
    expect(deleteCronJob).toHaveBeenCalledWith(JOB.id, 'research')
    expect($cronJobs.get()).toEqual([work])
  })

  it('does not treat stale aggregate confirmation as an actionable row', async () => {
    renderSection(vi.fn(), scope, [work, research])
    fireEvent.contextMenu(screen.getByText('research nightly'))
    fireEvent.click(await screen.findByRole('menuitem', { name: TRANSLATIONS.en.common.delete }))
    route.connection = 'other-gateway'
    await act(async () => { settleConfirm(true) })
    expect(deleteCronJob).not.toHaveBeenCalled()
  })

  it('does not read or mutate ownerless aggregate rows', async () => {
    renderSection(vi.fn(), scope, [JOB])
    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.showRuns }))
    fireEvent.contextMenu(screen.getByText('nightly'))
    expect(getCronJobRuns).not.toHaveBeenCalled()
    expect(screen.queryByRole('menuitem', { name: TRANSLATIONS.en.cron.pause })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: TRANSLATIONS.en.common.delete })).toBeNull()
  })
})

describe('SidebarCronJobsSection run rows', () => {
  it('loads an open peek immediately when a pending same-scope read settles', async () => {
    const scope = '\u0000work'
    commitCronJobsRequest(beginCronJobsRequest(scope), [JOB])
    renderSection(vi.fn(), scope)
    let pending!: ReturnType<typeof beginCronJobsRequest>
    act(() => { pending = beginCronJobsRequest(scope) })

    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.showRuns }))
    expect(getCronJobRuns).not.toHaveBeenCalled()

    act(() => { commitCronJobsRequest(pending, [JOB]) })
    expect(getCronJobRuns).toHaveBeenCalledWith(JOB.id, 5, 'work')
    expect(await screen.findByRole('button', { name: fmtDayTime.format(new Date(RUN.last_active * 1000)) })).toBeDefined()
  })

  it('passes the full run row to the owner-aware open callback', async () => {
    const onOpenRun = vi.fn()
    $showAllProfiles.set(false)
    renderSection(onOpenRun, '\u0000work')

    fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cron.showRuns }))

    const runButton = await screen.findByRole('button', {
      name: fmtDayTime.format(new Date(RUN.last_active * 1000))
    })

    fireEvent.click(runButton)

    expect(onOpenRun).toHaveBeenCalledWith(RUN.id, RUN)
  })
})
