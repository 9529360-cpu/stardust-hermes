import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import type { CronJob } from '@/types/hermes'

import { cronEditorUpdates, validateCronEditor } from './cron-job-model'
import {
  cronCreateRunPayload,
  cronEditRunUpdates,
  cronJobTriggerType,
  cronLastTriggeredAt,
  cronTriggerBadge,
  cronTriggerFormFromJob,
  type CronTriggerFormValues,
  DEFAULT_CRON_TRIGGER_FORM,
  folderDisplayName,
  validateCronTriggerForm
} from './cron-trigger-model'

const zh = TRANSLATIONS.zh.cron.trigger
const en = TRANSLATIONS.en.cron.trigger

function folderForm(overrides: Partial<CronTriggerFormValues> = {}): CronTriggerFormValues {
  return { when: 'folder', watchDir: 'D:\\Downloads', pattern: '*', ...overrides }
}

describe('cronCreateRunPayload', () => {
  it('sends the schedule and no trigger for a clock job', () => {
    expect(cronCreateRunPayload(DEFAULT_CRON_TRIGGER_FORM, ' 0 9 * * * ')).toEqual({ schedule: '0 9 * * *' })
  })

  it('sends a startup trigger and no schedule', () => {
    const payload = cronCreateRunPayload({ ...DEFAULT_CRON_TRIGGER_FORM, when: 'startup' }, '0 9 * * *')

    expect(payload).toEqual({ trigger: { type: 'startup' } })
    expect(payload).not.toHaveProperty('schedule')
  })

  it('sends the folder trigger with trimmed folder and pattern, and no schedule', () => {
    const payload = cronCreateRunPayload(folderForm({ watchDir: '  D:\\Inbox  ', pattern: ' *.pdf ' }), '0 9 * * *')

    expect(payload).toEqual({ trigger: { type: 'folder', watch_dir: 'D:\\Inbox', pattern: '*.pdf' } })
    expect(payload).not.toHaveProperty('schedule')
  })

  it('treats an empty pattern as every file', () => {
    expect(cronCreateRunPayload(folderForm({ pattern: '   ' }), '')).toEqual({
      trigger: { type: 'folder', watch_dir: 'D:\\Downloads', pattern: '*' }
    })
  })
})

describe('validateCronTriggerForm', () => {
  it('asks for nothing about the folder for a clock or startup job', () => {
    expect(validateCronTriggerForm(DEFAULT_CRON_TRIGGER_FORM)).toBeNull()
    expect(validateCronTriggerForm({ when: 'startup', watchDir: '', pattern: '' })).toBeNull()
  })

  it('requires a folder for a folder job', () => {
    expect(validateCronTriggerForm(folderForm({ watchDir: '   ' }))).toBe('folderRequired')
  })

  it.each(['D:\\Downloads', 'd:/Downloads', '\\\\server\\share\\in', '/home/me/inbox', '~/Downloads'])(
    'accepts the absolute or home-relative folder %s',
    watchDir => {
      expect(validateCronTriggerForm(folderForm({ watchDir }))).toBeNull()
    }
  )

  it.each(['Downloads', 'inbox\\pdf', 'C:', 'D:Downloads'])('refuses the relative folder %s', watchDir => {
    expect(validateCronTriggerForm(folderForm({ watchDir }))).toBe('folderNotAbsolute')
  })

  it('refuses a file-name pattern that contains a slash', () => {
    expect(validateCronTriggerForm(folderForm({ pattern: 'reports/*.pdf' }))).toBe('patternInvalid')
  })
})

describe('cronEditRunUpdates', () => {
  it('keeps the schedule for a clock job', () => {
    expect(cronEditRunUpdates('schedule', DEFAULT_CRON_TRIGGER_FORM, ' 0 9 * * * ')).toEqual({ schedule: '0 9 * * *' })
  })

  it('sends no run field for a startup job', () => {
    expect(cronEditRunUpdates('startup', DEFAULT_CRON_TRIGGER_FORM, '0 9 * * *')).toEqual({})
  })

  it('sends the folder and pattern for a folder job, and never a schedule', () => {
    const updates = cronEditRunUpdates('folder', folderForm({ watchDir: 'D:\\Inbox', pattern: '*.pdf' }), '0 9 * * *')

    expect(updates).toEqual({ trigger: { type: 'folder', watch_dir: 'D:\\Inbox', pattern: '*.pdf' } })
    expect(updates).not.toHaveProperty('schedule')
  })
})

describe('cronEditorUpdates by run type', () => {
  const base = { deliver: 'local', model: '', name: 'PDFs', prompt: 'summarise', provider: '' }

  it('omits the schedule and sends the folder trigger for a folder job', () => {
    const updates = cronEditorUpdates(
      { ...base, schedule: '0 9 * * *', trigger: folderForm({ watchDir: 'D:\\Inbox', pattern: '*.pdf' }) },
      { scriptOnlyJob: false, triggerType: 'folder' }
    )

    expect(updates).not.toHaveProperty('schedule')
    expect(updates.trigger).toEqual({ type: 'folder', watch_dir: 'D:\\Inbox', pattern: '*.pdf' })
    expect(updates.prompt).toBe('summarise')
  })

  it('omits the schedule for a startup job', () => {
    const updates = cronEditorUpdates(
      { ...base, schedule: '0 9 * * *', trigger: { ...DEFAULT_CRON_TRIGGER_FORM, when: 'startup' } },
      { scriptOnlyJob: false, triggerType: 'startup' }
    )

    expect(updates).not.toHaveProperty('schedule')
    expect(updates).not.toHaveProperty('trigger')
  })

  it('keeps the schedule for a clock job', () => {
    const updates = cronEditorUpdates({ ...base, schedule: '0 9 * * *', trigger: DEFAULT_CRON_TRIGGER_FORM }, {
      scriptOnlyJob: false
    })

    expect(updates.schedule).toBe('0 9 * * *')
    expect(updates).not.toHaveProperty('trigger')
  })
})

describe('validateCronEditor for event jobs', () => {
  it('needs no schedule for a startup or folder job', () => {
    expect(validateCronEditor({ prompt: 'go', schedule: '', scriptOnlyJob: false, triggerType: 'startup' })).toBeNull()
    expect(validateCronEditor({ prompt: 'go', schedule: '', scriptOnlyJob: false, triggerType: 'folder' })).toBeNull()
  })

  it('still needs a prompt for a folder job', () => {
    expect(validateCronEditor({ prompt: '', schedule: '', scriptOnlyJob: false, triggerType: 'folder' })).toBe('prompt')
  })

  it('still needs a schedule for a clock job', () => {
    expect(validateCronEditor({ prompt: 'go', schedule: '', scriptOnlyJob: false, triggerType: 'schedule' })).toBe(
      'schedule'
    )
  })
})

describe('folderDisplayName', () => {
  it.each([
    ['D:\\Downloads', 'Downloads'],
    ['D:/Downloads/', 'Downloads'],
    ['/home/me/inbox', 'inbox'],
    ['C:\\', 'C:'],
    ['/', '/']
  ])('shows %s as %s', (path, name) => {
    expect(folderDisplayName(path)).toBe(name)
  })
})

describe('cronTriggerBadge', () => {
  it('shows 定时 for a clock job in Chinese', () => {
    expect(cronTriggerBadge({ trigger: null }, zh)).toBe('定时')
  })

  it('shows 每次启动时 for a startup job in Chinese', () => {
    expect(cronTriggerBadge({ trigger: { type: 'startup' } }, zh)).toBe('每次启动时')
  })

  it('shows 文件夹: 名称 for a folder job in Chinese', () => {
    const job = { trigger: { type: 'folder' as const, watch_dir: 'D:\\Downloads', pattern: '*' } }

    expect(cronTriggerBadge(job, zh)).toBe('文件夹: Downloads')
  })

  it('reads the English copy in English, not the Chinese', () => {
    const job = { trigger: { type: 'folder' as const, watch_dir: '/srv/inbox', pattern: '*.pdf' } }

    expect(cronTriggerBadge(job, en)).toBe('Folder: inbox')
    expect(cronTriggerBadge({ trigger: { type: 'startup' } }, en)).toBe(en.badgeStartup)
  })
})

describe('cronLastTriggeredAt', () => {
  it('is null when an event job has never fired, so the card can show 从未', () => {
    expect(cronLastTriggeredAt({ trigger_status: { type: 'startup', last_triggered_at: null } })).toBeNull()
    expect(cronLastTriggeredAt({})).toBeNull()
  })

  it('returns the stored time once the job has fired', () => {
    expect(
      cronLastTriggeredAt({ trigger_status: { type: 'folder', last_triggered_at: '2026-10-11T09:30:00+08:00' } })
    ).toBe('2026-10-11T09:30:00+08:00')
  })
})

describe('cronJobTriggerType and the edit form', () => {
  it('treats a missing or schedule trigger as a clock job', () => {
    expect(cronJobTriggerType({})).toBe('schedule')
    expect(cronJobTriggerType({ trigger: { type: 'schedule' } })).toBe('schedule')
  })

  it('loads a folder job into the form with its folder and pattern', () => {
    const job = { trigger: { type: 'folder' as const, watch_dir: 'D:\\Inbox', pattern: '*.pdf' } } as Pick<CronJob, 'trigger'>

    expect(cronTriggerFormFromJob(job)).toEqual({ when: 'folder', watchDir: 'D:\\Inbox', pattern: '*.pdf' })
  })

  it('defaults the pattern of a folder job to every file', () => {
    const job = { trigger: { type: 'folder' as const, watch_dir: 'D:\\Inbox' } } as Pick<CronJob, 'trigger'>

    expect(cronTriggerFormFromJob(job).pattern).toBe('*')
  })
})
