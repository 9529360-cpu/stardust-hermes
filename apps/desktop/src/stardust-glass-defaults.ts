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

/**
 * Stardust is a glass-first product. The underlying Hermes translucency system
 * already provides native macOS Vibrancy and Windows 11 Acrylic/Mica, but its
 * historical untouched default only glasses the sidebar. Seed a whole-window
 * material once for a truly new/untuned install.
 *
 * Existing users are authoritative: the presence of either persisted
 * translucency key means the person (or an earlier release) has a window
 * preference, so we never overwrite it. The migration marker prevents a user
 * who later clears or changes settings from being "helpfully" reset.
 */
export function applyStardustGlassDefaults(winParam: string | null): void {
  if (!shouldEnableReferenceShell(winParam) || !GLASS_SUPPORTED) {
    return
  }

  if (readKey(STARDUST_GLASS_DEFAULTS_KEY) === '1') {
    return
  }

  const hasExistingPreference =
    readKey(TRANSLUCENCY_BOOK_KEY) !== null || readKey(LEGACY_TRANSLUCENCY_KEY) !== null

  if (!hasExistingPreference) {
    setTranslucencyMode('glass')
    setTranslucencyScope('window')
    setTranslucency(36)
  }

  writeKey(STARDUST_GLASS_DEFAULTS_KEY, '1')
}
