import { DEFAULT_REASONING_EFFORT, REASONING_EFFORT_VALUES } from '@hermes/shared'
import { describe, expect, it } from 'vitest'

import { type Locale, TRANSLATIONS } from '@/i18n'

import { isThinkingEnabled, reasoningEffortLabel, resolveReasoningEffort } from './reasoning-effort'

describe('reasoning-effort', () => {
  it('labels every level it claims to support', () => {
    for (const effort of REASONING_EFFORT_VALUES) {
      expect(reasoningEffortLabel(effort)).not.toBe('')
    }

    expect(reasoningEffortLabel('')).toBe('')
    // Unknown values pass through rather than silently reading as a real level.
    expect(reasoningEffortLabel('bogus')).toBe('bogus')
  })

  it('spells each level with the word Settings shows for it in the chosen locale', () => {
    // The composer pill and the Settings select read the same translated words.
    expect(reasoningEffortLabel('medium', TRANSLATIONS.zh)).toBe('中')
    expect(reasoningEffortLabel('xhigh', TRANSLATIONS.zh)).toBe('极高')
    expect(reasoningEffortLabel('none', TRANSLATIONS.zh)).toBe('关闭')
  })

  it('falls back to the active locale when no copy is passed', () => {
    // The SDK export has no React context; the runtime locale is English here.
    expect(reasoningEffortLabel('medium')).toBe(TRANSLATIONS.en.shell.modelOptions.medium)
    expect(reasoningEffortLabel('none')).toBe(TRANSLATIONS.en.settings.model.reasoningOff)
  })

  it('gives every non-English locale its own word for each level and for off', () => {
    // A locale that omits a key falls back to English, so its pill would read
    // in English on a translated screen. Every locale must differ from English.
    const english = REASONING_EFFORT_VALUES.map(effort => reasoningEffortLabel(effort, TRANSLATIONS.en))

    for (const locale of Object.keys(TRANSLATIONS) as Locale[]) {
      if (locale === 'en') {
        continue
      }

      REASONING_EFFORT_VALUES.forEach((effort, index) => {
        expect(reasoningEffortLabel(effort, TRANSLATIONS[locale]), `${locale} ${effort}`).not.toBe(english[index])
      })
    }
  })

  it('treats empty as inherit and only `none` as off', () => {
    expect(isThinkingEnabled('none')).toBe(false)
    expect(isThinkingEnabled('high')).toBe(true)
    // Empty inherits the fallback, so an off fallback reads as off.
    expect(isThinkingEnabled('', 'none')).toBe(false)
    expect(isThinkingEnabled('', 'high')).toBe(true)
  })

  it('resolves a scale value: inherit, off, or clamp', () => {
    expect(resolveReasoningEffort('high')).toBe('high')
    // Empty inherits the profile default rather than snapping to medium.
    expect(resolveReasoningEffort('', 'ultra')).toBe('ultra')
    // Off selects nothing on the scale.
    expect(resolveReasoningEffort('none')).toBe('')
    expect(resolveReasoningEffort('bogus')).toBe(DEFAULT_REASONING_EFFORT)
  })
})
