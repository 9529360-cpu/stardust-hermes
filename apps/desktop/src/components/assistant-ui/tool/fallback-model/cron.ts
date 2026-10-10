import { translateNow } from '@/i18n'
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

// The backend reports the raw delivery target. "origin" is the conversation that
// asked for the routine, "local" means save only; any other target is shown as sent.
const DELIVERY_COPY_KEYS: ReadonlyMap<string, string> = new Map([
  ['local', 'assistant.tool.cron.deliverySaveOnly'],
  ['origin', 'assistant.tool.cron.deliveryCurrentChat']
])

function cronDeliveryLabel(deliver: string): string {
  const key = DELIVERY_COPY_KEYS.get(deliver)

  return key ? translateNow(key) : deliver
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
export function cronPreviewDetail(preview: Record<string, unknown>): string {
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
