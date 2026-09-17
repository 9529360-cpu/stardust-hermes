import { describe, expect, it } from 'vitest'

import { hasUserTranslucencyPreference } from './stardust-glass-defaults'

describe('Stardust glass default migration', () => {
  it('treats a missing or untouched persisted book as safe to migrate', () => {
    expect(hasUserTranslucencyPreference(null, null)).toBe(false)
    expect(
      hasUserTranslucencyPreference(JSON.stringify({ mode: 'glass', base: {}, light: {}, dark: {} }), null)
    ).toBe(false)
  })

  it('preserves explicit clear mode and appearance/value edits', () => {
    expect(
      hasUserTranslucencyPreference(JSON.stringify({ mode: 'clear', base: {}, light: {}, dark: {} }), null)
    ).toBe(true)
    expect(
      hasUserTranslucencyPreference(
        JSON.stringify({ mode: 'glass', base: {}, light: {}, dark: { intensity: 12, scope: 'sidebar' } }),
        null
      )
    ).toBe(true)
  })

  it('preserves any legacy flat preference and repairs malformed untouched state', () => {
    expect(hasUserTranslucencyPreference(null, JSON.stringify({ intensity: 0 }))).toBe(true)
    expect(hasUserTranslucencyPreference('{not-json', null)).toBe(false)
  })
})
