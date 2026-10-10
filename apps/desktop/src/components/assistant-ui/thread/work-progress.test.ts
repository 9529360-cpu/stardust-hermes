import { describe, expect, it } from 'vitest'

import { workProgressItems } from './work-progress'

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
