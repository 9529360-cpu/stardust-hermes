import type { CronJob, CronJobCreatePayload, CronJobTrigger, CronJobUpdates, CronTriggerType } from '@/types/hermes'

// Form <-> payload rules for "When to run" (clock, startup, folder). Pure, so the create form, the
// edit form and the job cards read the same answer. The backend (cron/triggers.py) is the source of
// truth for what a folder may watch; the checks here only catch input the form can judge alone.

/** The run type of a stored job. A record with no usable trigger is a clock job. */
export function cronJobTriggerType(job: Pick<CronJob, 'trigger'>): CronTriggerType {
  const type = job.trigger?.type

  return type === 'startup' || type === 'folder' ? type : 'schedule'
}

export interface CronTriggerFormValues {
  when: CronTriggerType
  /** Folder to watch, as typed or picked. Only read when `when` is 'folder'. */
  watchDir: string
  /** File-name glob. An empty value means '*'. */
  pattern: string
}

export const DEFAULT_CRON_TRIGGER_FORM: CronTriggerFormValues = { when: 'schedule', watchDir: '', pattern: '*' }

export function cronTriggerFormFromJob(job: Pick<CronJob, 'trigger'>): CronTriggerFormValues {
  return {
    when: cronJobTriggerType(job),
    watchDir: job.trigger?.watch_dir ?? '',
    pattern: job.trigger?.pattern ?? '*'
  }
}

export type CronTriggerFormError = 'folderNotAbsolute' | 'folderRequired' | 'patternInvalid'

// Windows drive path (C:\ or C:/), UNC share, POSIX absolute path, or a home-relative path. The
// backend expands ~ and rejects anything that is still relative.
const ABSOLUTE_FOLDER = /^(?:[A-Za-z]:[\\/]|\\\\|\/|~)/

export function validateCronTriggerForm(values: CronTriggerFormValues): CronTriggerFormError | null {
  if (values.when !== 'folder') {
    return null
  }

  const folder = values.watchDir.trim()

  if (!folder) {
    return 'folderRequired'
  }

  if (!ABSOLUTE_FOLDER.test(folder)) {
    return 'folderNotAbsolute'
  }

  const pattern = values.pattern.trim()

  if (/[\\/]/.test(pattern) || pattern.includes('\u0000')) {
    return 'patternInvalid'
  }

  return null
}

function folderTrigger(values: CronTriggerFormValues): CronJobTrigger {
  return { type: 'folder', watch_dir: values.watchDir.trim(), pattern: values.pattern.trim() || '*' }
}

// One builder per run type. A clock job sends its schedule; an event job sends its trigger and no
// schedule, because the backend refuses a schedule on an event job.
const CREATE_RUN_FIELDS: Record<CronTriggerType, (values: CronTriggerFormValues, schedule: string) => Pick<CronJobCreatePayload, 'schedule' | 'trigger'>> = {
  folder: values => ({ trigger: folderTrigger(values) }),
  schedule: (_values, schedule) => ({ schedule: schedule.trim() }),
  startup: () => ({ trigger: { type: 'startup' } })
}

export function cronCreateRunPayload(
  values: CronTriggerFormValues,
  schedule: string
): Pick<CronJobCreatePayload, 'schedule' | 'trigger'> {
  return CREATE_RUN_FIELDS[values.when](values, schedule)
}

// The run type is fixed at creation (the backend refuses a change). A clock job updates its
// schedule, a startup job has no run field to change, and a folder job may move to another folder
// or pattern. Only the field that the job's own type owns is ever sent.
const EDIT_RUN_FIELDS: Record<CronTriggerType, (values: CronTriggerFormValues, schedule: string) => Pick<CronJobUpdates, 'schedule' | 'trigger'>> = {
  folder: values => ({ trigger: folderTrigger(values) }),
  schedule: (_values, schedule) => ({ schedule: schedule.trim() }),
  startup: () => ({})
}

export function cronEditRunUpdates(
  triggerType: CronTriggerType,
  values: CronTriggerFormValues,
  schedule: string
): Pick<CronJobUpdates, 'schedule' | 'trigger'> {
  return EDIT_RUN_FIELDS[triggerType](values, schedule)
}

/** Last path segment of a folder: 'D:\\Downloads' and 'D:/Downloads/' both give 'Downloads'. */
export function folderDisplayName(path: string): string {
  const trimmed = path.trim().replace(/[\\/]+$/, '')
  const last = trimmed.split(/[\\/]/).pop() ?? ''

  return last || trimmed || path.trim()
}

export interface CronTriggerBadgeCopy {
  badgeFolder: (name: string) => string
  badgeSchedule: string
  badgeStartup: string
}

/** Short badge for a job card: 定时, 每次启动时, or 文件夹: <folder name>. */
export function cronTriggerBadge(job: Pick<CronJob, 'trigger'>, copy: CronTriggerBadgeCopy): string {
  const triggerType = cronJobTriggerType(job)

  if (triggerType === 'folder') {
    return copy.badgeFolder(folderDisplayName(job.trigger?.watch_dir ?? ''))
  }

  return triggerType === 'startup' ? copy.badgeStartup : copy.badgeSchedule
}

/** Stored ISO time of the last trigger, or null when an event job has never fired. */
export function cronLastTriggeredAt(job: Pick<CronJob, 'trigger_status'>): null | string {
  return job.trigger_status?.last_triggered_at || null
}
