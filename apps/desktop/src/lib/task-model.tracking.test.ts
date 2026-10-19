import { describe, expect, it } from 'vitest'

import { groupTaskRows, LINGER_MS, type TaskRow, trackTaskRows } from './task-model'

const row = (id: string, state = 'running'): TaskRow => ({
  id,
  phase: { kind: 'none' },
  rail: 'process',
  state,
  title: id
})

describe('task row tracking', () => {
  it('keeps a completion timestamp stable across source updates so it can move to recent', () => {
    const running = trackTaskRows([], [row('a')], 1000)
    const finished = trackTaskRows(running, [row('a', 'completed')], 2000)
    const refreshed = trackTaskRows(finished, [row('a', 'completed')], 3000)

    expect(refreshed[0].finishedAt).toBe(2000)
    expect(groupTaskRows(refreshed, 2000 + LINGER_MS)[0].id).toBe('recent')
  })

  it('preserves arrival order through source reordering and appends new tasks', () => {
    const initial = trackTaskRows([], [row('a'), row('b')], 1000)
    const updated = trackTaskRows(initial, [row('b'), row('a'), row('c')], 2000)

    expect(groupTaskRows(updated, 2000)[0].rows.map(item => item.id)).toEqual(['a', 'b', 'c'])
    expect(updated.find(item => item.id === 'a')?.observedAt).toBe(1000)
    expect(updated.find(item => item.id === 'c')?.observedAt).toBe(2000)
  })

  it('clears an old finish timestamp when the same task resumes', () => {
    const complete = trackTaskRows([], [row('a', 'completed')], 1000)
    const resumed = trackTaskRows(complete, [row('a')], 2000)
    const finishedAgain = trackTaskRows(resumed, [row('a', 'completed')], 3000)

    expect(resumed[0].finishedAt).toBeUndefined()
    expect(finishedAgain[0].finishedAt).toBe(3000)
  })
})
