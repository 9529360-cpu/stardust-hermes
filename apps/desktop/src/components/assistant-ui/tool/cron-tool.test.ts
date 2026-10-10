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
    expect(view.detail).toContain('Delivery: origin')
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
