import { DEFAULT_REASONING_EFFORT, isReasoningEffort } from '@hermes/shared'

import { TRANSLATIONS } from '@/i18n/catalog'
import { getRuntimeI18nLocale } from '@/i18n/runtime'
import type { Translations } from '@/i18n/types'
import { normalize } from '@/lib/text'

/** The word for a level in one locale: the `shell.modelOptions` words the
 *  Settings select shows, and `settings.model.reasoningOff` for off. Surfaces
 *  pass their `useI18n().t` so a locale switch re-renders them; a caller with no
 *  React context (a plugin) gets the active locale. */
export function reasoningEffortLabel(effort: string, t: Translations = TRANSLATIONS[getRuntimeI18nLocale()]): string {
  const key = normalize(effort)

  if (!key) {
    return ''
  }

  if (key === 'none') {
    return t.settings.model.reasoningOff
  }

  return isReasoningEffort(key) ? t.shell.modelOptions[key] : effort
}

/** Thinking is on unless a level explicitly says otherwise; an empty value
 *  means "inherit", so it resolves through `fallback` first. */
export const isThinkingEnabled = (effort: string, fallback: string = DEFAULT_REASONING_EFFORT): boolean =>
  normalize(effort || fallback) !== 'none'

/** The level a scale control should show. Empty inherits `fallback`; `none`
 *  (thinking off) selects nothing; anything unrecognized clamps to the default. */
export function resolveReasoningEffort(effort: string, fallback: string = DEFAULT_REASONING_EFFORT): string {
  const value = normalize(effort || fallback)

  if (value === 'none') {
    return ''
  }

  return isReasoningEffort(value) ? value : DEFAULT_REASONING_EFFORT
}
