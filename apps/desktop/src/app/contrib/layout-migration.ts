import { $activePresetId } from '@/components/pane-shell/tree/store'
import { readKey, writeKey } from '@/lib/storage'
import { applyDesktopLayoutPreset } from '@/store/pane-focus'
import { isAuxiliaryWindow } from '@/store/windows'

const PERSONAL_LAYOUT_VERSION = 4
const PERSONAL_LAYOUT_VERSION_KEY = 'hermes.desktop.personalLayoutVersion'

/**
 * Stardust's default desktop shell is a product contract, not a theme. When
 * that contract changes, existing installs that are still on the stock
 * `default` preset must move with it; explicitly selected/custom layouts stay
 * authoritative and are left untouched.
 */
export function schedulePersonalLayoutMigration(): void {
  if (isAuxiliaryWindow()) {
    return
  }

  const current = Number(readKey(PERSONAL_LAYOUT_VERSION_KEY) ?? 0)

  if (Number.isFinite(current) && current >= PERSONAL_LAYOUT_VERSION) {
    return
  }

  queueMicrotask(() => {
    if ($activePresetId.get() === 'default') {
      applyDesktopLayoutPreset('default')
    }

    writeKey(PERSONAL_LAYOUT_VERSION_KEY, String(PERSONAL_LAYOUT_VERSION))
  })
}
