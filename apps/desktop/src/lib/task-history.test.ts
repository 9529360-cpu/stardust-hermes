import { beforeEach, describe, expect, it } from 'vitest'

import { $taskHistoryByScope, clearTaskHistory, readTaskHistory, recordTaskHistory } from './task-history'
import { RECENT_EXPANDED_LIMIT, RECENT_EXPANDED_WINDOW_MS, type TaskRow } from './task-model'

const scope = { connectionId: 'primary', profileId: 'default', sessionId: 'runtime-1' }
const row = (id: string, state = 'completed'): TaskRow => ({
  id,
  state,
  title: id,
  rail: 'process',
  phase: { kind: 'none' }
})

beforeEach(() => $taskHistoryByScope.set({}))

describe('session-owned task history independent of task page lifetime', () => {
  it('accumulates completed work even with no mounted task page', () => {
    recordTaskHistory(scope, [row('first')], 1000)
    recordTaskHistory(scope, [row('second')], 2000)
    expect(readTaskHistory(scope, 3000).map(item => item.id)).toEqual(['second', 'first'])
  })

  it('retains timestamps across repeated producer snapshots and view remounts', () => {
    recordTaskHistory(scope, [row('first')], 1000)
    recordTaskHistory(scope, [row('first')], 2000)
    expect(readTaskHistory(scope, 3000)[0].finishedAt).toBe(1000)
    expect(readTaskHistory(scope, 4000)[0].finishedAt).toBe(1000)
  })

  it('does not capture unknown or live states and forgets a row that resumes', () => {
    recordTaskHistory(scope, [row('first')], 1000)
    recordTaskHistory(
      scope,
      [row('first', 'running'), row('future', 'new_state'), row('question', 'waiting_for_user')],
      2000
    )
    expect(readTaskHistory(scope, 3000)).toEqual([])
  })

  it('keeps different profile and connection owners separate even with identical runtime ids', () => {
    recordTaskHistory(scope, [row('original')], 1000)
    recordTaskHistory({ ...scope, connectionId: 'remote' }, [row('remote-only')], 1000)
    recordTaskHistory({ ...scope, profileId: 'work' }, [row('work-only')], 1000)
    expect(readTaskHistory({ ...scope, sessionId: '*' }, 2000).map(item => item.id)).toEqual(['original'])
  })

  it('caps the global budget across sessions and evicts the oldest scope first', () => {
    for (let bucket = 0; bucket < 5; bucket++) {
      recordTaskHistory(
        { ...scope, sessionId: `runtime-${bucket}` },
        Array.from({ length: RECENT_EXPANDED_LIMIT }, (_, i) => row(`${bucket}:${i}`)),
        1000 + bucket
      )
    }
    const allScopes = Object.values($taskHistoryByScope.get())
    expect(allScopes.flat()).toHaveLength(200)
    expect(allScopes.every(items => items.length <= RECENT_EXPANDED_LIMIT)).toBe(true)
    expect(readTaskHistory({ ...scope, sessionId: 'runtime-0' }, 2000)).toEqual([])
    expect(readTaskHistory({ ...scope, sessionId: 'runtime-4' }, 2000)).toHaveLength(RECENT_EXPANDED_LIMIT)
  })

  it.each([{ connectionId: '' }, { profileId: '' }, { sessionId: '' }, { sessionId: '*' }])(
    'rejects incomplete or wildcard producer scope %j without altering valid history',
    invalid => {
      recordTaskHistory(scope, [row('original')], 1000)
      const before = $taskHistoryByScope.get()
      recordTaskHistory({ ...scope, ...invalid }, [row('unowned')], 2000)
      expect($taskHistoryByScope.get()).toBe(before)
      expect(readTaskHistory(scope, 3000).map(item => item.id)).toEqual(['original'])
      expect(readTaskHistory({ ...scope, connectionId: '' }, 3000)).toEqual([])
      expect(readTaskHistory({ ...scope, profileId: '' }, 3000)).toEqual([])
    }
  )

  it('bounds and expires history and clears a reset session', () => {
    for (let i = 0; i < RECENT_EXPANDED_LIMIT + 5; i++) {
      recordTaskHistory(scope, [row(String(i))], 1000 + i)
    }
    const retained = readTaskHistory(scope, 2000)
    expect(retained).toHaveLength(RECENT_EXPANDED_LIMIT)
    expect(retained.some(item => item.id === '0')).toBe(false)
    expect(readTaskHistory(scope, 2000 + RECENT_EXPANDED_WINDOW_MS)).toEqual([])
    clearTaskHistory(scope)
    expect(readTaskHistory(scope, 2000)).toEqual([])
  })
})
