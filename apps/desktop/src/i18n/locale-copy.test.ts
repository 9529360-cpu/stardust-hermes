import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE } from './languages'
import type { Locale } from './types'

// A translated screen must not show English for these: the Projects empty line and the
// Tools and Plugins page titles. A locale that forgets a key silently falls back to English.
const english = TRANSLATIONS[DEFAULT_LOCALE]
const translatedLocales = (Object.keys(TRANSLATIONS) as Locale[]).filter(locale => locale !== DEFAULT_LOCALE)

describe('desktop locale bundles', () => {
  for (const locale of translatedLocales) {
    it(`${locale} translates the Projects empty line and the Tools and Plugins page titles`, () => {
      const copy = TRANSLATIONS[locale]

      expect(copy.sidebar.projects.noProjects).not.toBe(english.sidebar.projects.noProjects)
      expect(copy.skills.pageTitles.toolsets).not.toBe(english.skills.pageTitles.toolsets)
      expect(copy.skills.pageTitles.plugins).not.toBe(english.skills.pageTitles.plugins)
    })
  }
})
