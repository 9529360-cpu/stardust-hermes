import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

describe('desktop semantic surface tokens', () => {
  it('keeps input backgrounds theme-derived instead of pinning a light literal', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')
    const declaration = styles.match(/--ui-bg-input:\s*([^;]+);/)

    expect(declaration).not.toBeNull()
    expect(declaration?.[1]).toMatch(/var\(--(?:ui|theme)-|color-mix\(/)
    expect(declaration?.[1]).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i)
  })
})