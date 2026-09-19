import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { buildRailTasks } from './activity'

const session = (overrides: Partial<SessionInfo> = {}): SessionInfo =>
  ({
    id: 'tip',
    _lineage_root_id: 'root',
    title: 'Research task',
    last_active: 123,
    started_at: 100,
    ...overrides
  }) as SessionInfo

describe('activity task projection', () => {
  it('deduplicates session lineage aliases and lets waiting-for-user outrank running', () => {
    const tasks = buildRailTasks(['root', 'tip'], ['root'], [session()], null, {})

    expect(tasks).toEqual([
      {
        id: 'session:tip',
        label: 'Research task',
        detail: 'Waiting for your input',
        status: 'waiting',
        updatedAt: 123_000
      }
    ])
  })

  it('normalizes backend session timestamps to milliseconds before sorting with renderer tasks', () => {
    const tasks = buildRailTasks(
      ['tip'],
      [],
      [session({ last_active: 10 })],
      null,
      {
        doctor: {
          status: { exit_code: null, lines: [], name: 'doctor', pid: 1, running: true },
          updatedAt: 10_500
        }
      }
    )

    expect(tasks.map(task => task.id)).toEqual(['action:doctor', 'session:tip'])
  })

  it('projects preview restart failures without creating a second task owner', () => {
    const tasks = buildRailTasks(
      [],
      [],
      [],
      { message: 'server failed', status: 'error', taskId: 'p1', url: 'http://x' },
      {}
    )

    expect(tasks).toHaveLength(1)
    expect(tasks[0]).toMatchObject({
      id: 'preview:p1',
      detail: 'server failed',
      status: 'error'
    })
  })
})
