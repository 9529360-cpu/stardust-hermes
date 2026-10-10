import type { ApprovalAuditEntry, ApprovalGrant } from '@hermes/shared'

import type { Translations } from '@/i18n/types'

type ActivityCopy = Translations['settings']['activity']

/** `allowed` and `blocked` drive the tone of a decision; everything else reads as neutral. */
export type DecisionTone = 'allowed' | 'blocked' | 'neutral'

const ALLOWED_OUTCOMES = new Set(['approved_once', 'approved_session', 'approved_permanent', 'auto_approved'])
const BLOCKED_OUTCOMES = new Set(['blocked', 'denied'])

export function decisionTone(outcome: string): DecisionTone {
  if (ALLOWED_OUTCOMES.has(outcome)) {
    return 'allowed'
  }

  return BLOCKED_OUTCOMES.has(outcome) ? 'blocked' : 'neutral'
}

/** The product sentence for an outcome. An outcome the copy does not know yet reads as "recorded", never as a raw key. */
export function outcomeLabel(outcome: string, c: ActivityCopy): string {
  const labels = c.outcome as Record<string, string>

  return Object.prototype.hasOwnProperty.call(labels, outcome) ? labels[outcome] : c.outcome.unknown
}

export interface GrantView {
  title: string
  detail: string
}

/** A standing grant as a sentence the user can act on. The pattern itself is what the user approved, so it stays visible. */
export function describeGrant(grant: ApprovalGrant, c: ActivityCopy, locale: string, now: Date): GrantView {
  const title =
    grant.action_kind === 'send_message'
      ? c.grantSend(grant.target)
      : grant.action_kind === 'command_pattern'
        ? c.grantCommand(grant.target)
        : grant.target

  const created = parseDate(grant.created_at)

  return {
    title,
    detail: created ? c.grantSince(formatDay(created, locale, now)) : ''
  }
}

export interface DecisionRow {
  id: string
  title: string
  outcome: string
  tone: DecisionTone
  time: string
}

export interface DecisionGroup {
  key: string
  label: string
  rows: DecisionRow[]
}

/**
 * Recent decisions, newest first, grouped by local calendar day ("Today", "Yesterday", then a date).
 * Entries without a readable timestamp cannot be placed on a day, so they are left out.
 */
export function groupDecisions(
  entries: ApprovalAuditEntry[],
  c: ActivityCopy,
  locale: string,
  now: Date
): DecisionGroup[] {
  const groups = new Map<string, DecisionGroup>()

  const dated = entries
    .map((entry, index) => ({ entry, index, date: parseDate(entry.ts) }))
    .filter((item): item is { entry: ApprovalAuditEntry; index: number; date: Date } => item.date !== null)
    .sort((a, b) => b.date.getTime() - a.date.getTime() || a.index - b.index)

  for (const { entry, index, date } of dated) {
    const key = dayKey(date)
    let group = groups.get(key)

    if (!group) {
      group = { key, label: dayLabel(date, c, locale, now), rows: [] }
      groups.set(key, group)
    }

    group.rows.push({
      id: `${entry.ts}-${index}`,
      title: decisionTitle(entry, c),
      outcome: outcomeLabel(entry.outcome, c),
      tone: decisionTone(entry.outcome),
      time: date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    })
  }

  return [...groups.values()]
}

function decisionTitle(entry: ApprovalAuditEntry, c: ActivityCopy): string {
  const subject = entry.command_preview || entry.description

  if (subject && (entry.command_preview || entry.tool_name === 'terminal')) {
    return c.confirmationCommand(subject)
  }

  return c.confirmationTool(entry.tool_name)
}

function parseDate(value: string): Date | null {
  const time = Date.parse(value)

  return Number.isNaN(time) ? null : new Date(time)
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
}

function dayLabel(date: Date, c: ActivityCopy, locale: string, now: Date): string {
  if (dayKey(date) === dayKey(now)) {
    return c.dayToday
  }

  const yesterday = new Date(now)

  yesterday.setDate(yesterday.getDate() - 1)

  return dayKey(date) === dayKey(yesterday) ? c.dayYesterday : formatDay(date, locale, now)
}

/** A short date. The year appears only when it is not the current year. */
function formatDay(date: Date, locale: string, now: Date): string {
  return date.toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' })
  })
}
