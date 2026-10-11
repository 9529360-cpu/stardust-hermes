import { translateNow } from '@/i18n'
import { capitalize, firstStringField } from '@/lib/text'
import { fmtDayTime } from '@/lib/time'

import { isRecord } from './format'

// The backend tool is `cronjob_manage` (tools/cronjob_tools.py). `cronjob` is the
// legacy name that model_tools.py still aliases to it, so either one can reach
// the card: both render through it.
const CRON_TOOL_NAMES: ReadonlySet<string> = new Set(['cronjob', 'cronjob_manage'])

export function isCronTool(name: string): boolean {
  return CRON_TOOL_NAMES.has(name)
}

export function cronScalar(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim()
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value)
  }

  return ''
}

// A dry run (`dry_run: true`) previewed a create or update and saved nothing, so
// its body is a preview, never a saved job. Refused dry runs (`success: false`)
// are not previews and return null, as does every other result.
export function cronPreview(resultRecord: Record<string, unknown>): Record<string, unknown> | null {
  if (resultRecord.dry_run !== true || resultRecord.success !== true) {
    return null
  }

  return isRecord(resultRecord.preview) ? resultRecord.preview : {}
}

// The `action` values of cronjob_manage, each with the bundle key of its label.
// `run_now` and `trigger` are the backend's aliases of `run`.
const ACTION_LABEL_KEYS: ReadonlyMap<string, string> = new Map([
  ['create', 'create'],
  ['list', 'list'],
  ['update', 'update'],
  ['pause', 'pause'],
  ['resume', 'resume'],
  ['remove', 'remove'],
  ['run', 'run'],
  ['run_now', 'run'],
  ['trigger', 'run'],
  ['resnap', 'refresh'],
  ['manage', 'manage']
])

// The label of a backend action. An action this table does not know keeps its own name.
function cronActionLabel(action: string): string {
  const key = ACTION_LABEL_KEYS.get(action)

  return key ? translateNow(`assistant.tool.cron.actions.${key}`) : capitalize(action)
}

// The backend reports the raw delivery target. "origin" is the conversation that
// asked for the routine, "local" means save only, and "all" means every connected
// channel. Any other target (a platform, a chat id) is shown as the backend sent it.
const DELIVERY_LABEL_KEYS: ReadonlyMap<string, string> = new Map([
  ['all', 'assistant.tool.cron.deliveryAll'],
  ['local', 'assistant.tool.cron.deliverySaveOnly'],
  ['origin', 'assistant.tool.cron.deliveryCurrentChat']
])

function cronDeliveryLabel(deliver: string): string {
  const key = DELIVERY_LABEL_KEYS.get(deliver)

  return key ? translateNow(key) : deliver
}

// The backend's repeat display (`_repeat_display` in tools/cronjob_job_args.py).
// Progress such as "1/3" is a count the backend already words, so it stays as sent.
const REPEAT_LABEL_KEYS: ReadonlyMap<string, string> = new Map([
  ['forever', 'assistant.tool.cron.repeatForever'],
  ['once', 'assistant.tool.cron.repeatOnce']
])

function cronRepeatLabel(repeat: string): string {
  const key = REPEAT_LABEL_KEYS.get(repeat)

  if (key) {
    return translateNow(key)
  }

  const times = /^(\d+) times$/.exec(repeat)

  return times ? translateNow('assistant.tool.cron.repeatTimes', Number(times[1])) : repeat
}

function cronRunTime(iso: string): string {
  const ts = Date.parse(iso)

  return Number.isNaN(ts) ? iso : fmtDayTime.format(ts)
}

// The line that says what a dry run is. It is the row title, because a collapsed
// row shows only its title: a preview must not read as a saved job there. Empty
// for anything that is not a preview.
export function cronPreviewHeadline(resultRecord: Record<string, unknown>): string {
  const preview = cronPreview(resultRecord)

  if (!preview) {
    return ''
  }

  const schedule = cronScalar(preview.schedule)

  if (schedule) {
    return translateNow('assistant.tool.cron.previewWithSchedule', schedule)
  }

  return translateNow('assistant.tool.cron.preview')
}

// The body is rendered as markdown, where a single newline would run lines together
// and a line after a list would join its last item. Blank lines keep each block apart.
function cronPreviewDetail(preview: Record<string, unknown>): string {
  const blocks: string[] = []

  const previous = cronScalar(preview.previous_schedule)

  if (previous) {
    blocks.push(`${translateNow('assistant.tool.cron.previousSchedule')}: ${previous}`)
  }

  const runs = Array.isArray(preview.next_runs) ? preview.next_runs.map(cronScalar).filter(Boolean) : []

  if (runs.length) {
    blocks.push(translateNow('assistant.tool.cron.nextRuns'), runs.map(run => `- ${cronRunTime(run)}`).join('\n'))
  }

  const deliver = cronScalar(preview.deliver)

  if (deliver) {
    blocks.push(`${translateNow('assistant.tool.cron.delivery')}: ${cronDeliveryLabel(deliver)}`)
  }

  blocks.push(translateNow('assistant.tool.cron.notSaved'))

  return blocks.join('\n\n')
}

// The job a result describes. A create reports its fields at the top level; an update,
// pause, resume or run nests the job under `job`, and a remove names it under
// `removed_job`. Top-level fields win when both are present.
function cronJobFields(resultRecord: Record<string, unknown>): Record<string, unknown> {
  const removed = isRecord(resultRecord.removed_job) ? resultRecord.removed_job : {}
  const job = isRecord(resultRecord.job) ? resultRecord.job : {}

  return { ...removed, ...job, ...resultRecord }
}

// The subtitle of a cron result: the preview's headline, the job count of a list, the
// backend's sentence, or else the action and the job's name.
export function cronSubtitle(argsRecord: Record<string, unknown>, resultRecord: Record<string, unknown>): string {
  const headline = cronPreviewHeadline(resultRecord)

  if (headline) {
    return headline
  }

  const jobs = Array.isArray(resultRecord.jobs) ? resultRecord.jobs : null

  if (jobs) {
    return jobs.length
      ? translateNow('assistant.tool.cron.jobCount', jobs.length)
      : translateNow('assistant.tool.cron.noJobs')
  }

  // The backend's sentence is data. The card never draws the subtitle, only checks
  // whether the detail repeats it, so the English sentence shows nowhere.
  const message = firstStringField(resultRecord, ['message'])

  if (message) {
    return message
  }

  const action = cronActionLabel(firstStringField(argsRecord, ['action']) || 'manage')

  const name =
    firstStringField(cronJobFields(resultRecord), ['name']) || firstStringField(argsRecord, ['name', 'job_id'])

  return name
    ? translateNow('assistant.tool.titleTemplates.actionTarget', action, name)
    : translateNow('assistant.tool.cron.actionOnly', action)
}

// The labelled fields a saved job reports, in one paragraph. The detail area is only
// about two lines tall, so one row per line would scroll the next run out of sight.
function cronJobDetail(fields: Record<string, unknown>): string {
  const nextRun = cronScalar(fields.next_run_at)

  const rows: [string, string][] = [
    [translateNow('assistant.tool.cron.schedule'), cronScalar(fields.schedule)],
    [translateNow('assistant.tool.cron.repeat'), cronRepeatLabel(cronScalar(fields.repeat))],
    [translateNow('assistant.tool.cron.delivery'), cronDeliveryLabel(cronScalar(fields.deliver))],
    [translateNow('assistant.tool.cron.nextRun'), nextRun ? cronRunTime(nextRun) : '']
  ]

  return rows
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${value}`)
    .join(' · ')
}

function cronJobListDetail(jobs: unknown[]): string {
  if (!jobs.length) {
    return translateNow('assistant.tool.cron.noJobsScheduled')
  }

  return jobs
    .slice(0, 20)
    .map(job => {
      const row = isRecord(job) ? job : {}
      const name = firstStringField(row, ['name', 'id']) || translateNow('assistant.tool.cron.untitledJob')
      const sched = firstStringField(row, ['schedule_display', 'schedule'])

      return sched ? `- ${name} · ${sched}` : `- ${name}`
    })
    .join('\n')
}

// The detail of a cron result. A result with none of the cron fields has nothing to
// label, so it shows nothing rather than the generic summary's raw keys.
export function cronDetail(resultRecord: Record<string, unknown>): string {
  const preview = cronPreview(resultRecord)

  if (preview) {
    return cronPreviewDetail(preview)
  }

  // A refused dry run has no preview to show; its error is already the subtitle.
  if (resultRecord.dry_run === true) {
    return ''
  }

  const jobs = Array.isArray(resultRecord.jobs) ? resultRecord.jobs : null

  if (jobs) {
    return cronJobListDetail(jobs)
  }

  return cronJobDetail(cronJobFields(resultRecord))
}
