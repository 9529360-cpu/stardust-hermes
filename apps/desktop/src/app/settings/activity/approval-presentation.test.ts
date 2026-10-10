import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'

import { decisionTone, describeGrant, groupDecisions, outcomeLabel } from './approval-presentation'

const c = en.settings.activity
const NOW = new Date(2026, 9, 10, 12, 0)

function at(day: number, hour: number, minute = 0, month = 9): string {
  return new Date(2026, month, day, hour, minute).toISOString()
}

function entry(ts: string, overrides: Partial<Record<string, string>> = {}) {
  return {
    ts,
    session_key: 'session-1',
    kind: 'command',
    tool_name: 'terminal',
    description: '',
    pattern_key: '',
    outcome: 'approved_once',
    mode: 'manual',
    command_preview: 'git status',
    ...overrides
  }
}

describe('decisionTone', () => {
  it('reads allowed outcomes as allowed and refusals as blocked', () => {
    expect(decisionTone('approved_once')).toBe('allowed')
    expect(decisionTone('auto_approved')).toBe('allowed')
    expect(decisionTone('denied')).toBe('blocked')
    expect(decisionTone('blocked')).toBe('blocked')
  })

  it('keeps outcomes it does not recognise neutral instead of guessing', () => {
    expect(decisionTone('notify_failed')).toBe('neutral')
    expect(decisionTone('something_new')).toBe('neutral')
  })
})

describe('outcomeLabel', () => {
  it('turns a known outcome into a sentence', () => {
    expect(outcomeLabel('approved_permanent', c)).toBe('Always allowed')
  })

  it('falls back to "Recorded" for unknown outcomes, including prototype names', () => {
    expect(outcomeLabel('something_new', c)).toBe('Recorded')
    expect(outcomeLabel('constructor', c)).toBe('Recorded')
  })
})

describe('groupDecisions', () => {
  it('groups newest first under Today, Yesterday and a date, and drops unreadable timestamps', () => {
    const groups = groupDecisions(
      [
        entry(at(9, 20), { command_preview: 'npm test' }),
        entry(at(10, 9), { command_preview: 'git status' }),
        entry(at(10, 11), { command_preview: 'git diff' }),
        entry('not-a-date'),
        entry(at(1, 10, 0, 8), { outcome: 'denied', command_preview: 'rm -rf build' })
      ],
      c,
      'en',
      NOW
    )

    expect(groups.map(group => group.label)).toEqual(['Today', 'Yesterday', expect.stringContaining('Sep')])
    expect(groups[0].rows.map(row => row.title)).toEqual(['Run git diff', 'Run git status'])
    expect(groups[1].rows.map(row => row.title)).toEqual(['Run npm test'])
    expect(groups[2].rows[0]).toMatchObject({ title: 'Run rm -rf build', outcome: 'Declined', tone: 'blocked' })
  })

  it('names a non-command decision after its tool', () => {
    const [group] = groupDecisions(
      [entry(at(10, 9), { kind: 'message', tool_name: 'send_message', command_preview: '', description: '' })],
      c,
      'en',
      NOW
    )

    expect(group.rows[0].title).toBe('Use send_message')
  })
})

describe('describeGrant', () => {
  it('states what was approved in plain words and keeps the pattern visible', () => {
    const view = describeGrant(
      {
        id: 'grant-1',
        action_kind: 'command_pattern',
        target: 'git status*',
        created_at: at(9, 9)
      },
      c,
      'en',
      NOW
    )

    expect(view.title).toBe('Run git status* without asking')
    expect(view.detail).toMatch(/^Allowed /)
  })

  it('describes a send grant by its recipient', () => {
    const view = describeGrant(
      { id: 'grant-2', action_kind: 'send_message', target: 'Sam', created_at: at(9, 9) },
      c,
      'en',
      NOW
    )

    expect(view.title).toBe('Send messages to Sam without asking')
  })
})
