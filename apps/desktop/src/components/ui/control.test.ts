import { describe, expect, it } from 'vitest'

import { controlVariants } from './control'

const fixedGeometry = [
  { height: 'h-6', radius: 'rounded-[2.5px]', size: 'xs' },
  { height: 'h-7', radius: 'rounded-[2.5px]', size: 'compact' },
  { height: 'h-8', radius: 'rounded-[6px]', size: 'sm' },
  { height: 'h-9', radius: 'rounded-[6px]', size: 'md' }
] as const

describe('controlVariants fixed field sizes', () => {
  it.each(fixedGeometry)('$size owns $height height and $radius radius', ({ height, radius, size }) => {
    const classes = controlVariants({ size }).split(/\s+/)

    expect(classes).toContain(height)
    expect(classes).toContain(radius)
    expect(classes).not.toContain(radius === 'rounded-[6px]' ? 'rounded-[2.5px]' : 'rounded-[6px]')
  })
})
