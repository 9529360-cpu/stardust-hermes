import { describe, expect, it } from 'vitest'

import { segmentedControlNextIndex } from './segmented-control-navigation'

describe('segmentedControlNextIndex', () => {
  it.each([
    [0, 3, 'ArrowRight', false, 1],
    [2, 3, 'ArrowRight', false, 0],
    [0, 3, 'ArrowLeft', false, 2],
    [1, 3, 'ArrowRight', true, 0],
    [2, 3, 'ArrowLeft', true, 0],
    [2, 3, 'Home', true, 0],
    [0, 3, 'End', false, 2],
    [0, 1, 'ArrowRight', false, 0]
  ] as const)('navigates %s / %s with %s (RTL %s)', (index, count, key, rtl, expected) => {
    expect(segmentedControlNextIndex(index, count, key, rtl)).toBe(expected)
  })

  it('does not claim unrelated keys or empty controls', () => {
    for (const key of ['Tab', 'Escape', 'Enter', ' ', 'ArrowUp', 'ArrowDown']) {
      expect(segmentedControlNextIndex(0, 3, key)).toBeNull()
    }
    expect(segmentedControlNextIndex(0, 0, 'Home')).toBeNull()
  })
})
