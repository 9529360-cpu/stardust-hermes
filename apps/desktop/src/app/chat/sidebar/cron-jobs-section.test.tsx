import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, TRANSLATIONS } from '@/i18n'
import { fmtDayTime } from '@/lib/time'
import type { CronJob, SessionInfo } from '@/types/hermes'

import { SidebarCronJobsSection } from './cron-jobs-section'

const { getCronJobRuns } = vi.hoisted(() => ({ getCronJobRuns: vi.fn() }))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<object>()),
  getCronJobRuns
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
  getCronJobRuns.mockResolvedValue([RUN])
})

afterEach(() => {
  cleanup()
  getCronJobRuns.mockReset()
})

function renderSection(onOpenRun: (sessionId: string, session?: SessionInfo) => void) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <SidebarCronJobsSection
        jobs={[JOB]}
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
