import type { WorkItem } from '@hermes/shared'
import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'
import type { CronJob } from '@/types/hermes'

import {
  jobLookupKey,
  type RecentRun,
  runDeliveryLabel,
  runErrorText,
  runStatus,
  runTimeLabel,
  runTitle,
  sortRunsNewestFirst
} from './recent-runs'

const c = en.cron.recentRuns

function run(overrides: Partial<WorkItem> & { profile?: string } = {}): RecentRun {
  return {
    id: 'cron:exec-1',
    kind: 'cron',
    title: 'Cron job job-1',
    status: 'completed',
    started_at: 1_790_000_000,
    updated_at: 1_790_000_060,
    detail: { job_id: 'job-1' },
    profile: 'default',
    ...overrides
  }
}

describe('runStatus', () => {
  it('maps the backend statuses onto what the user sees', () => {
    expect(runStatus(run({ status: 'running' }))).toBe('running')
    expect(runStatus(run({ status: 'completed' }))).toBe('completed')
    expect(runStatus(run({ status: 'failed' }))).toBe('failed')
    expect(runStatus(run({ status: 'interrupted' }))).toBe('interrupted')
  })

  it('reads a cancelled scheduled run as interrupted, and an unknown status as unknown', () => {
    expect(runStatus(run({ status: 'cancelled' }))).toBe('interrupted')
    expect(runStatus(run({ status: 'surprise' as WorkItem['status'] }))).toBe('unknown')
  })
})

describe('runDeliveryLabel', () => {
  it('states where the result went in a sentence', () => {
    expect(runDeliveryLabel(run({ detail: { delivery_outcome: 'delivered' } }), c)).toBe('Delivered')
    expect(runDeliveryLabel(run({ detail: { delivery_outcome: 'failed' } }), c)).toBe("Couldn't deliver")
    expect(runDeliveryLabel(run({ detail: { delivery_outcome: 'local' } }), c)).toBe('Saved on this device')
    expect(runDeliveryLabel(run({ detail: { delivery_outcome: 'suppressed_acked' } }), c)).toBe(
      'Nothing to send this time'
    )
  })

  it('says nothing when no delivery was recorded or the outcome is not one it knows', () => {
    expect(runDeliveryLabel(run({ detail: {} }), c)).toBeNull()
    expect(runDeliveryLabel(run({ detail: { delivery_outcome: 'constructor' } }), c)).toBeNull()
  })
})

describe('runTitle', () => {
  it('names the run after its job, and falls back when the job was removed', () => {
    const job = { id: 'job-1', name: 'Morning summary', prompt: 'summarize', profile: '' } as unknown as CronJob
    const jobs = new Map([[jobLookupKey('default', 'job-1'), job]])

    expect(runTitle(run(), jobs, c)).toBe('Morning summary')
    expect(runTitle(run({ detail: { job_id: 'gone' } }), jobs, c)).toBe('Scheduled task')
  })
})

describe('runErrorText', () => {
  it('returns the recorded error, trimmed, and nothing for a non-text value', () => {
    expect(runErrorText(run({ detail: { error: '  provider unavailable  ' } }))).toBe('provider unavailable')
    expect(runErrorText(run({ detail: { error: 42 } }))).toBe('')
    expect(runErrorText(run({ detail: {} }))).toBe('')
  })
})

describe('sortRunsNewestFirst', () => {
  it('orders by the latest update and puts runs without a timestamp last', () => {
    const sorted = sortRunsNewestFirst([
      run({ id: 'old', updated_at: 100 }),
      run({ id: 'none', updated_at: null, started_at: null }),
      run({ id: 'new', updated_at: 300 })
    ])

    expect(sorted.map(item => item.id)).toEqual(['new', 'old', 'none'])
  })
})

describe('runTimeLabel', () => {
  it('shows a dash for a missing or unreadable time and a clock time otherwise', () => {
    expect(runTimeLabel(null, 'en')).toBe('—')
    expect(runTimeLabel(0, 'en')).toBe('—')
    expect(runTimeLabel(1_790_000_000, 'en')).toMatch(/\d/)
  })

  it('adds the day when asked, for runs that are not from today', () => {
    expect(runTimeLabel(1_790_000_000, 'en', true).length).toBeGreaterThan(runTimeLabel(1_790_000_000, 'en').length)
  })
})

describe('jobLookupKey', () => {
  it('treats an empty owner as the launch profile', () => {
    expect(jobLookupKey('', 'job-1')).toBe(jobLookupKey('default', 'job-1'))
  })
})
