import { describe, expect, it } from 'vitest'

import { ar } from './ar'
import { en } from './en'
import { ja } from './ja'
import { ru } from './ru'
import { zh } from './zh'
import { zhHant } from './zh-hant'

const locales = [
  ['en', en],
  ['zh', zh],
  ['zh-hant', zhHant],
  ['ja', ja],
  ['ru', ru],
  ['ar', ar]
] as const

describe('profile clone disclosure', () => {
  it.each(locales)('%s discloses the private state copied by normal profile cloning', (_locale, copy) => {
    for (const message of [copy.profiles.cloneFromDesc, copy.profiles.cloneFromDefaultDesc]) {
      expect(message).toContain('.env')
      expect(message).toContain('MEMORY.md')
      expect(message).toContain('USER.md')
    }
  })
})
