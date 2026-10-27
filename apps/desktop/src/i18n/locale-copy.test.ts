import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE } from './languages'
import type { Locale } from './types'

// A translated screen must not show English for these: the Projects empty line and the
// Tools and Plugins page titles. A locale that forgets a key silently falls back to English.
const english = TRANSLATIONS[DEFAULT_LOCALE]
const translatedLocales = (Object.keys(TRANSLATIONS) as Locale[]).filter(locale => locale !== DEFAULT_LOCALE)

describe('desktop locale bundles', () => {
  for (const locale of ['en', 'zh', 'ja', 'zh-hant'] as const) {
    it(`${locale} supplies the connectors and messaging page copy`, () => {
      const copy = TRANSLATIONS[locale]
      expect(copy.connectors.pageTitle).toBeTruthy()
      expect(copy.connectors.pageDescription).toBeTruthy()
      expect(copy.connectors.connectedCount(2)).toContain('2')
      expect(copy.messaging.defaultDescription('Email')).toContain('Email')
    })
  }

  for (const locale of translatedLocales) {
    it(`${locale} translates the Projects empty line and the Tools and Plugins page titles`, () => {
      const copy = TRANSLATIONS[locale]

      expect(copy.sidebar.projects.emptyTitle).not.toBe(english.sidebar.projects.emptyTitle)
      expect(copy.skills.pageTitles.toolsets).not.toBe(english.skills.pageTitles.toolsets)
      expect(copy.skills.pageTitles.plugins).not.toBe(english.skills.pageTitles.plugins)
    })

    it(`${locale} translates the retained Webhook delivery targets`, () => {
      const options = TRANSLATIONS[locale].webhooks.deliverOptions
      const englishOptions = english.webhooks.deliverOptions

      for (const key of ['whatsapp_cloud', 'weixin', 'github_comment'] as const) {
        expect(options[key]).not.toBe(key)
        expect(options[key]).not.toBe(englishOptions[key])
      }
    })
  }
})
