import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'

// The Hermes Cloud connection mode and its Nous Cloud recovery copy are retired
// (a private install with no migration). Every bundle is merged onto en, so
// walking each locale's merged copy catches a key left behind in any one bundle;
// the source check catches literal copy that a key walk would not see.
const LOCALES = ['en', 'zh', 'zh-hant', 'ja', 'ru', 'ar'] as const

function cloudKeyPaths(node: unknown, path: string[] = []): string[] {
  if (!node || typeof node !== 'object') {
    return []
  }

  return Object.entries(node).flatMap(([key, value]) => {
    const keyPath = [...path, key]

    return [...(/cloud/i.test(key) ? [keyPath.join('.')] : []), ...cloudKeyPaths(value, keyPath)]
  })
}

describe('retired Hermes Cloud copy', () => {
  for (const locale of LOCALES) {
    it(`${locale} ships no Hermes Cloud or Nous Cloud keys`, () => {
      expect(cloudKeyPaths(TRANSLATIONS[locale])).toEqual([])
    })

    it(`${locale} bundle source carries no Hermes Cloud or Nous Cloud copy`, () => {
      const source = readFileSync(resolve(process.cwd(), 'src/i18n', `${locale}.ts`), 'utf8')

      expect(source).not.toMatch(/Hermes Cloud|Nous Cloud/)
    })
  }
})
