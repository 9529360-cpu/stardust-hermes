import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'

// The desktop speaks as Stardust. The inherited name survives only where it still
// names an executable that is still called `hermes` (the SSH path field and its
// recovery copy).

const LOCALES = ['en', 'zh', 'zh-hant', 'ja', 'ru', 'ar'] as const

const INHERITED_NAME_ALLOWED = [
  'Hermes path (optional)',
  'set the Hermes path.',
  'Hermes 路径（可选）',
  '设置 Hermes 路径。',
  'Hermes 路徑（選用）',
  '設定 Hermes 路徑。',
  'Hermes パス（任意）',
  'Hermes パスを設定',
  'Путь к Hermes (необязательно)',
  'путь к Hermes.'
]

const STANDALONE_BRAND = /(?<![A-Za-z0-9_])Hermes(?![A-Za-z0-9_])/

function bundleSource(locale: string): string {
  return readFileSync(resolve(process.cwd(), 'src/i18n', `${locale}.ts`), 'utf8')
}

describe('desktop copy names the product Stardust', () => {
  for (const locale of LOCALES) {
    it(`${locale} uses the inherited name only in the allowed compatibility phrases`, () => {
      let source = bundleSource(locale)

      for (const phrase of INHERITED_NAME_ALLOWED) {
        source = source.replaceAll(phrase, '')
      }

      expect(source.match(STANDALONE_BRAND)).toBeNull()
    })
  }

  it('Chinese bundles no longer use the older 星尘 or 星塵 name', () => {
    for (const locale of ['zh', 'zh-hant'] as const) {
      expect(bundleSource(locale)).not.toContain('星尘')
      expect(bundleSource(locale)).not.toContain('星塵')
    }
  })

  it('English possessives read "Stardust\'s", not the Hermes-era "Hermes\'" form', () => {
    expect(bundleSource('en')).not.toMatch(/Stardust' [a-z]/)
    expect(bundleSource('en')).toContain("Stardust's background service")
  })

  it('every bundle gives the same Stardust installer command for a missing remote hermes', () => {
    for (const locale of Object.keys(TRANSLATIONS) as Array<keyof typeof TRANSLATIONS>) {
      expect(TRANSLATIONS[locale].settings.gateway.sshErrNotInstalled).toContain(
        'curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash'
      )
    }
  })

  it('install copy points at the Stardust installer, never the upstream script', () => {
    for (const locale of LOCALES) {
      expect(bundleSource(locale)).not.toContain('hermes-agent.nousresearch.com/install.sh')
    }

    expect(bundleSource('en')).toContain('stardust-hermes/main/scripts/install-stardust.sh')
  })
})
