import { describe, expect, it } from 'vitest'

import { isFoldableProgressMessage, workProgressItems } from './work-progress'

describe('workProgressItems', () => {
  it('groups only adjacent eligible updates and leaves other messages in place', () => {
    expect(workProgressItems([0, 1, 2, 3, 4, 5], new Set([1, 2, 4]))).toEqual([
      { kind: 'message', index: 0 },
      { kind: 'progress', indices: [1, 2] },
      { kind: 'message', index: 3 },
      { kind: 'progress', indices: [4] },
      { kind: 'message', index: 5 }
    ])
  })
})

describe('isFoldableProgressMessage', () => {
  const sealed = (custom: Record<string, unknown>) => ({
    content: [{ type: 'text' }],
    metadata: { custom },
    role: 'assistant',
    status: { type: 'complete' }
  })

  it('folds sealed commentary', () => {
    expect(isFoldableProgressMessage(sealed({ interim: true }))).toBe(true)
  })

  it('never folds a verify-on-stop final candidate the user already saw', () => {
    expect(isFoldableProgressMessage(sealed({ finalCandidate: true, interim: true }))).toBe(false)
  })
})
