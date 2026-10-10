import type { WorkItem } from '@hermes/shared'

import type { Translations } from '@/i18n/types'
import type { CronJob } from '@/types/hermes'

import { jobTitle } from './job-state'

type RecentRunsCopy = Translations['cron']['recentRuns']

/** A scheduled run tagged with the profile it was read from. The launch profile is named 'default'. */
export interface RecentRun extends WorkItem {
  profile: string
}

/** What the user sees for a run. `cancelled` cannot happen to a scheduled run, so it reads as interrupted. */
export type RunStatus = 'running' | 'completed' | 'failed' | 'interrupted' | 'unknown'

const RUN_STATUS: Record<string, RunStatus> = {
  cancelled: 'interrupted',
  completed: 'completed',
  failed: 'failed',
  interrupted: 'interrupted',
  running: 'running'
}

export function runStatus(run: WorkItem): RunStatus {
  return RUN_STATUS[run.status] ?? 'unknown'
}

export const RUN_DOT: Record<RunStatus, string> = {
  completed: 'bg-(--ui-text-quaternary)',
  failed: 'bg-destructive',
  interrupted: 'bg-amber-500',
  running: 'bg-primary',
  unknown: 'bg-muted-foreground'
}

export const RUN_TONE: Record<RunStatus, 'bad' | 'good' | 'muted' | 'warn'> = {
  completed: 'muted',
  failed: 'bad',
  interrupted: 'warn',
  running: 'good',
  unknown: 'muted'
}

export function runKey(run: RecentRun): string {
  return `${run.profile}\u0000${run.id}`
}

/** The job key the runs and the job list share. An empty owner means the launch profile. */
export function jobLookupKey(profile: string | null | undefined, jobId: string): string {
  return `${profile || 'default'}\u0000${jobId}`
}

export function runJobId(run: WorkItem): string {
  const id = run.detail?.job_id

  return typeof id === 'string' ? id : ''
}

export function runJob(run: RecentRun, jobsByKey: ReadonlyMap<string, CronJob>): CronJob | undefined {
  return jobsByKey.get(jobLookupKey(run.profile, runJobId(run)))
}

/** The job's name when the job still exists, otherwise a neutral label. The backend deliberately sends only the id. */
export function runTitle(run: RecentRun, jobsByKey: ReadonlyMap<string, CronJob>, c: RecentRunsCopy): string {
  const job = runJob(run, jobsByKey)

  return job ? jobTitle(job) : c.unknownJob
}

export function runStatusLabel(run: WorkItem, c: RecentRunsCopy): string {
  return c.status[runStatus(run)]
}

const DELIVERY_COPY: Record<string, (c: RecentRunsCopy) => string> = {
  delivered: c => c.deliveryDelivered,
  failed: c => c.deliveryFailed,
  local: c => c.deliveryLocal,
  not_configured: c => c.deliveryNotConfigured,
  queued: c => c.deliveryQueued,
  suppressed: c => c.deliverySilent,
  suppressed_acked: c => c.deliverySilent
}

/** Where the result went, in a sentence. Null when the backend has not recorded a delivery yet. */
export function runDeliveryLabel(run: WorkItem, c: RecentRunsCopy): string | null {
  const outcome = run.detail?.delivery_outcome

  if (typeof outcome !== 'string' || !Object.prototype.hasOwnProperty.call(DELIVERY_COPY, outcome)) {
    return null
  }

  return DELIVERY_COPY[outcome](c)
}

/** The error the run recorded. The backend has already redacted it before it reaches the desktop. */
export function runErrorText(run: WorkItem): string {
  const error = run.detail?.error

  return typeof error === 'string' ? error.trim() : ''
}

/** A run timestamp (unix seconds) in the app's locale. `withDate` adds the day for anything that is not today's. */
export function runTimeLabel(seconds: number | null, locale: string, withDate = false): string {
  if (!seconds) {
    return '—'
  }

  const date = new Date(seconds * 1000)

  if (Number.isNaN(date.getTime())) {
    return '—'
  }

  const time = date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })

  return withDate ? `${date.toLocaleDateString(locale, { day: 'numeric', month: 'short' })} ${time}` : time
}

/** Newest first, with runs that have no timestamp last. */
export function sortRunsNewestFirst(runs: readonly RecentRun[]): RecentRun[] {
  return [...runs].sort((a, b) => (b.updated_at ?? b.started_at ?? 0) - (a.updated_at ?? a.started_at ?? 0))
}
