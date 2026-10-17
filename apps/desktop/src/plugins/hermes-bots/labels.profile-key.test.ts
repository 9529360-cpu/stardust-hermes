import { describe, expect, it } from 'vitest'

import { profileKeyFromFriendlyName } from './labels'

describe('profileKeyFromFriendlyName', () => {
  it('generates the same internal key for the same normalized friendly name', () => {
    expect(profileKeyFromFriendlyName('Alex Smith')).toBe(profileKeyFromFriendlyName(' Alex Smith '))
  })

  it('creates a stable profile key for a Chinese name', () => {
    expect(profileKeyFromFriendlyName('小安')).toBe('partner-1hsi71u')
  })

  it('does not turn a blank name into a default profile', () => {
    expect(profileKeyFromFriendlyName('  ')).toBe('')
  })

  it('leaves unrepresentable names empty instead of assigning a shared partner key', () => {
    expect(profileKeyFromFriendlyName('!!!')).toBe('')
  })

  it('does not collide when different names normalize to the same slug', () => {
    expect(profileKeyFromFriendlyName('Alex Smith')).not.toBe(profileKeyFromFriendlyName('Alex-Smith'))
  })

  it('chooses another deterministic key when the friendly-name hash collides', () => {
    const first = 'Alex=Smith=Smith:Smith@Smith!Smith!Smith!Smith!Smith'
    const second = 'Alex;Smith&Smith=Smith*Smith!Smith!Smith!Smith!Smith'
    const occupied = new Set([profileKeyFromFriendlyName(first)])

    expect(profileKeyFromFriendlyName(second, occupied)).not.toBe(profileKeyFromFriendlyName(first))
  })

  it('keeps long Unicode names with the same prefix distinct', () => {
    const prefix = '長'.repeat(40)
    const first = profileKeyFromFriendlyName(`${prefix}甲`)
    const second = profileKeyFromFriendlyName(`${prefix}乙`)

    expect(first).not.toBe(second)
    expect(first.length).toBeLessThanOrEqual(64)
    expect(second.length).toBeLessThanOrEqual(64)
  })
})
