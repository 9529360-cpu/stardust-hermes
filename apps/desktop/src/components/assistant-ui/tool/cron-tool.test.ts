import { afterEach, describe, expect, it } from 'vitest'

import { setRuntimeI18nLocale, translateNow } from '@/i18n'
import { fmtDayTime } from '@/lib/time'

import { buildToolView, isCronTool, type ToolPart } from './fallback-model'

const CRON_NAMES = ['cronjob_manage', 'cronjob'] as const

const part = (overrides: Partial<ToolPart>): ToolPart => ({
  args: {},
  isError: false,
  result: {},
  toolCallId: 'call_cron',
  toolName: 'cronjob_manage',
  type: 'tool-call',
  ...overrides
})

const NEXT_RUNS = ['2026-10-12T09:00:00+08:00', '2026-10-13T09:00:00+08:00']

// Dry-run create: the backend previews the routine and saves nothing.
const dryRunCreate = {
  success: true,
  dry_run: true,
  saved: false,
  preview: {
    name: 'Morning digest',
    schedule: 'every day 09:00',
    next_runs: NEXT_RUNS,
    deliver: 'origin',
    repeat: null,
    paused: false
  },
  message: 'Preview only, nothing was saved.'
}

const withPreview = (fields: Record<string, unknown>) => ({
  ...dryRunCreate,
  preview: { ...dryRunCreate.preview, ...fields }
})

// Real create: the saved-job shape the cron card has always rendered.
const realCreate = {
  success: true,
  job_id: 'job_1',
  name: 'Morning digest',
  schedule: 'every day 09:00',
  repeat: 'forever',
  deliver: 'origin',
  next_run_at: '2026-10-12T09:00:00+08:00',
  job: { id: 'job_1', name: 'Morning digest' },
  message: "Cron job 'Morning digest' created."
}

const COPY_KEYS = [
  'preview',
  'previousSchedule',
  'nextRuns',
  'delivery',
  'deliveryCurrentChat',
  'deliverySaveOnly',
  'notSaved'
] as const

afterEach(() => {
  setRuntimeI18nLocale('en')
})

describe('cron tool name', () => {
  it('accepts the backend tool name and its legacy alias', () => {
    expect(isCronTool('cronjob_manage')).toBe(true)
    expect(isCronTool('cronjob')).toBe(true)
  })

  it('rejects unrelated tool names', () => {
    expect(isCronTool('terminal')).toBe(false)
    expect(isCronTool('web_search')).toBe(false)
    expect(isCronTool('cronjob_manager')).toBe(false)
  })

  it.each(CRON_NAMES)('titles a %s call as a cron job, not a generic tool', name => {
    expect(buildToolView(part({ toolName: name, result: undefined }), '').title).toBe('Scheduling cron job')
    expect(buildToolView(part({ toolName: name, result: realCreate }), '').title).toBe('Cron job')
  })
})

describe('cron dry-run preview', () => {
  it.each(CRON_NAMES)('shows a %s dry run as a preview with its schedule', name => {
    const view = buildToolView(part({ toolName: name, result: dryRunCreate }), '')

    expect(view.subtitle).toContain('every day 09:00')
    expect(view.subtitle).toContain('preview')
    // The collapsed row shows only the title, so the title must say it too.
    expect(view.title).toBe('Cron job preview · every day 09:00')
  })

  it.each(CRON_NAMES)('lists the next runs, the delivery target and a not-saved line for a %s dry run', name => {
    const { detail } = buildToolView(part({ toolName: name, result: dryRunCreate }), '')

    for (const iso of NEXT_RUNS) {
      expect(detail).toContain(fmtDayTime.format(Date.parse(iso)))
    }

    expect(detail).toContain('Current chat')
    expect(detail).toContain('Not saved')
    expect(detail).not.toContain('undefined')
  })

  it('maps the save-only target and passes any other delivery target through', () => {
    const saveOnly = buildToolView(part({ result: withPreview({ deliver: 'local' }) }), '')
    expect(saveOnly.detail).toContain('Save only')
    expect(saveOnly.detail).not.toContain('local')

    const telegram = buildToolView(part({ result: withPreview({ deliver: 'telegram:12345' }) }), '')
    expect(telegram.detail).toContain('Delivery: telegram:12345')
  })

  it('renders the preview copy in the active locale', () => {
    setRuntimeI18nLocale('zh')
    const view = buildToolView(part({ result: dryRunCreate }), '')

    expect(view.title).toContain('定时任务预览')
    expect(view.subtitle).toContain('定时任务预览')
    expect(view.detail).toContain('当前对话')
    expect(view.detail).toContain('未保存')
    expect(view.detail).not.toContain('Not saved')
  })

  it('renders nothing for preview fields the backend leaves out', () => {
    const view = buildToolView(part({ result: { success: true, dry_run: true, saved: false, preview: {} } }), '')

    expect(view.title).toBe('Cron job preview')
    expect(view.subtitle).toBe('Cron job preview')
    expect(view.detail).toBe('Not saved')
    expect(`${view.subtitle}\n${view.detail}`).not.toContain('undefined')
  })

  it('shows a refused dry run as an error, not as a preview', () => {
    const view = buildToolView(part({ result: { success: false, dry_run: true, error: 'schedule is required' } }), '')

    expect(view.status).toBe('error')
    expect(view.title).toBe('Cron job')
    expect(view.subtitle).toBe('schedule is required')
    expect(view.detail).not.toContain('Not saved')
  })
})

describe('cron real result', () => {
  it.each(CRON_NAMES)('keeps the saved-job subtitle and detail for a %s create', name => {
    const view = buildToolView(part({ toolName: name, result: realCreate }), '')

    expect(view.subtitle).toBe("Cron job 'Morning digest' created.")
    expect(view.detail).toContain('Schedule: every day 09:00')
    expect(view.detail).toContain('Delivery: Current chat')
    expect(view.detail).not.toContain('Not saved')
  })
})

describe('cron preview copy per locale', () => {
  it.each(['zh', 'zh-hant', 'ja', 'ru', 'ar'] as const)('translates every preview string for %s', locale => {
    const copy = (active: typeof locale | 'en') => {
      setRuntimeI18nLocale(active)

      return COPY_KEYS.map(key => translateNow(`assistant.tool.cron.${key}`))
    }

    const english = copy('en')
    const translated = copy(locale)

    COPY_KEYS.forEach((key, index) => {
      expect(translated[index], key).not.toBe(english[index])
    })
  })
})

// The backend's English sentence for a routine that saves its output locally (the desktop default).
const LOCAL_RETURN =
  'This job saves its output locally and its non-silent completion will return to the Desktop/TUI conversation that created it as a durable background result.'

const savedJob = {
  job_id: 'job_1',
  name: 'Morning digest',
  schedule: 'every day 09:00',
  repeat: 'forever',
  deliver: 'origin',
  next_run_at: '2026-10-12T09:00:00+08:00',
  state: 'scheduled',
  enabled: true
}

describe('cron result in Chinese', () => {
  it('shows a saved routine in Chinese: the title and labelled rows, with no English left', () => {
    setRuntimeI18nLocale('zh')

    const view = buildToolView(
      part({
        args: { action: 'create', name: 'Morning digest' },
        result: { ...realCreate, message: `Cron job 'Morning digest' created. ${LOCAL_RETURN}` }
      }),
      ''
    )

    expect(view.title).toBe('定时任务')
    expect(view.detail).toContain('排程: every day 09:00')
    expect(view.detail).toContain('重复: 永久')
    expect(view.detail).toContain('投递: 当前对话')
    expect(view.detail).toContain('下次运行: ')
    expect(view.detail).not.toMatch(/Schedule:|Repeat:|Delivery:|Next run:|forever|origin/)
  })

  it('shows a removed routine and a refreshed one with the same Chinese labels', () => {
    setRuntimeI18nLocale('zh')

    const removed = buildToolView(
      part({
        args: { action: 'remove', job_id: 'job_1' },
        result: {
          success: true,
          message: "Cron job 'Morning digest' removed.",
          removed_job: { id: 'job_1', name: 'Morning digest', schedule: 'every day 09:00' }
        }
      }),
      ''
    )

    expect(removed.title).toBe('定时任务')
    expect(removed.detail).toBe('排程: every day 09:00')

    const refreshed = buildToolView(
      part({
        args: { action: 'resnap', job_id: 'job_1' },
        result: { success: true, message: 'Cron job refreshed.', job: savedJob }
      }),
      ''
    )

    expect(refreshed.detail).toContain('排程: every day 09:00')
    expect(refreshed.detail).toContain('投递: 当前对话')
    expect(refreshed.detail).not.toMatch(/Schedule|Delivery/)
  })

  it('shows the empty states of a list in Chinese', () => {
    setRuntimeI18nLocale('zh')

    const list = (jobs: unknown[]) =>
      buildToolView(part({ args: { action: 'list' }, result: { success: true, count: jobs.length, jobs } }), '')

    expect(list([savedJob, { ...savedJob, job_id: 'job_2', name: 'Weekly' }]).subtitle).toBe('2 个定时任务')
    expect(list([savedJob]).detail).toBe('- Morning digest · every day 09:00')
    expect(list([]).subtitle).toBe('没有定时任务')
    expect(list([]).detail).toBe('没有已安排的定时任务')
  })

  it('names the action and reads the nested job for an update in Chinese', () => {
    setRuntimeI18nLocale('zh')

    const view = buildToolView(
      part({ args: { action: 'update', job_id: 'job_1' }, result: { success: true, job: savedJob } }),
      ''
    )

    expect(view.subtitle).toBe('更新 Morning digest')
    expect(view.detail).toContain('排程: every day 09:00')
    expect(view.detail).toContain('投递: 当前对话')
    expect(view.detail).not.toMatch(/Success|Enabled|State|Job/)
  })

  it('reads the nested job for a pause in English too, with no raw JSON key as a label', () => {
    const view = buildToolView(
      part({ args: { action: 'pause', job_id: 'job_1' }, result: { success: true, job: savedJob } }),
      ''
    )

    expect(view.subtitle).toBe('Pause Morning digest')
    expect(view.detail).toContain('Schedule: every day 09:00')
    expect(view.detail).toContain('Delivery: Current chat')
    expect(view.detail).not.toMatch(/Success|Enabled|State|Job/)
  })
})

// Every string the cron card reads, with sample arguments for the ones that take them.
const CRON_COPY: readonly { args?: unknown[]; key: string }[] = [
  { args: ['Pause'], key: 'assistant.tool.cron.actionOnly' },
  { key: 'assistant.tool.cron.actions.create' },
  { key: 'assistant.tool.cron.actions.update' },
  { key: 'assistant.tool.cron.actions.pause' },
  { key: 'assistant.tool.cron.actions.resume' },
  { key: 'assistant.tool.cron.actions.remove' },
  { key: 'assistant.tool.cron.actions.run' },
  { key: 'assistant.tool.cron.actions.list' },
  { key: 'assistant.tool.cron.actions.refresh' },
  { key: 'assistant.tool.cron.actions.manage' },
  { key: 'assistant.tool.cron.deliveryAll' },
  { args: [2], key: 'assistant.tool.cron.jobCount' },
  { key: 'assistant.tool.cron.nextRun' },
  { key: 'assistant.tool.cron.noJobs' },
  { key: 'assistant.tool.cron.noJobsScheduled' },
  { key: 'assistant.tool.cron.repeat' },
  { key: 'assistant.tool.cron.repeatForever' },
  { key: 'assistant.tool.cron.repeatOnce' },
  { args: [3], key: 'assistant.tool.cron.repeatTimes' },
  { key: 'assistant.tool.cron.schedule' },
  { key: 'assistant.tool.cron.untitledJob' },
  { key: 'assistant.tool.titles.cronjob.done' },
  { key: 'assistant.tool.titles.cronjob.pending' },
  { key: 'assistant.tool.titles.cronjob.pendingAction' }
]

describe('cron copy in every locale', () => {
  it.each(['zh', 'zh-hant', 'ja', 'ru', 'ar'] as const)('translates every cron string for %s', locale => {
    for (const { args = [], key } of CRON_COPY) {
      setRuntimeI18nLocale('en')
      const english = translateNow(key, ...args)

      setRuntimeI18nLocale(locale)
      const translated = translateNow(key, ...args)

      expect(translated, key).not.toBe(key)
      expect(translated, key).not.toBe(english)
    }
  })

  it.each(['zh', 'zh-hant'] as const)('writes every cron string in Chinese for %s', locale => {
    setRuntimeI18nLocale(locale)

    for (const { args = [], key } of CRON_COPY) {
      expect(translateNow(key, ...args), key).toMatch(/[一-鿿]/)
    }
  })
})
