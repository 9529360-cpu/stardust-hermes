import { readKey, writeKey } from '@/lib/storage'
import {
  GLASS_SUPPORTED,
  setTranslucency,
  setTranslucencyMode,
  setTranslucencyScope
} from '@/store/translucency'

import { shouldEnableReferenceShell } from './reference-shell'

const TRANSLUCENCY_BOOK_KEY = 'hermes.desktop.translucency.v2'
const LEGACY_TRANSLUCENCY_KEY = 'hermes.desktop.translucency.v1'
const STARDUST_GLASS_DEFAULTS_KEY = 'stardust.desktop.glassDefaults.v1'

type PersistedBook = {
  base?: unknown
  dark?: unknown
  light?: unknown
  mode?: unknown
}

const hasOwnValues = (value: unknown): boolean =>
  Boolean(value && typeof value === 'object' && Object.keys(value as Record<string, unknown>).length > 0)

/**
 * The translucency store persists the normalized BOOK even when nobody touched
 * the setting. That untouched payload is essentially
 * `{ mode: 'glass', base: {}, light: {}, dark: {} }`; key presence alone is
 * therefore not evidence of a user preference.
 */
export function hasUserTranslucencyPreference(bookRaw: null | string, legacyRaw: null | string): boolean {
  // A legacy flat state came from a real pre-book setting. Preserve it even at
  // zero: explicitly turning translucency off is still a preference.
  if (legacyRaw !== null) {
    return true
  }

  if (bookRaw === null) {
    return false
  }

  try {
    const book = JSON.parse(bookRaw) as PersistedBook

    return book.mode === 'clear' || hasOwnValues(book.base) || hasOwnValues(book.light) || hasOwnValues(book.dark)
  } catch {
    // Malformed persisted state is not useful preference evidence; the store's
    // own normalizer also falls back safely, so let Stardust repair the default.
    return false
  }
}

/**
 * Stardust is a glass-first product. The underlying translucency system already
 * provides native macOS Vibrancy and Windows 11 Acrylic/Mica, but the historic
 * untouched default glasses only the sidebar. Seed whole-window material once
 * for a genuinely untuned install.
 *
 * Real user edits are authoritative. The separate migration marker means a
 * person who changes the setting after this migration will never be reset.
 */
export function applyStardustGlassDefaults(winParam: string | null): void {
  if (!shouldEnableReferenceShell(winParam) || !GLASS_SUPPORTED) {
    return
  }

  if (readKey(STARDUST_GLASS_DEFAULTS_KEY) === '1') {
    return
  }

  const hasExistingPreference = hasUserTranslucencyPreference(
    readKey(TRANSLUCENCY_BOOK_KEY),
    readKey(LEGACY_TRANSLUCENCY_KEY)
  )

  if (!hasExistingPreference) {
    setTranslucencyMode('glass')
    setTranslucencyScope('window')
    setTranslucency(36)
  }

  writeKey(STARDUST_GLASS_DEFAULTS_KEY, '1')
}
