import { describe, expect, it } from 'vitest'

import { controlVariants } from './control'

const fixedGeometry = [
  { height: 'h-7', radius: 'rounded-[3px]', size: 'xs' },
  { height: 'h-8', radius: 'rounded-[4px]', size: 'compact' },
  { height: 'h-8', radius: 'rounded-[4px]', size: 'sm' },
  { height: 'h-9', radius: 'rounded-[6px]', size: 'md' }
] as const

describe('controlVariants fixed field sizes', () => {
  it.each(fixedGeometry)('$size owns $height height and $radius radius', ({ height, radius, size }) => {
    const classes = controlVariants({ size }).split(/\s+/)

    expect(classes).toContain(height)
    expect(classes).toContain(radius)
    if (size === 'xs' || size === 'compact') {
      expect(classes).not.toContain('rounded-[6px]')
    }
  })
})
