import { atom } from 'nanostores'

import { persistBoolean, persistString, storedBoolean, storedString } from '@/lib/storage'

const KEY = 'hermes.desktop.backdrop.v1'
const IMAGE_KEY = 'stardust.desktop.backdrop-image.v1'

/** Whether the optional image backdrop renders behind the desktop window. */
export const $backdrop = atom(storedBoolean(KEY, false))

/**
 * Device-local image path for the window backdrop.
 *
 * Only the path is persisted. The renderer reads the bytes through Electron on
 * this machine when it paints; the image is never sent to the gateway, model,
 * Memory, or profile state.
 */
export const $backdropImagePath = atom<null | string>(storedString(IMAGE_KEY))

$backdrop.subscribe(on => persistBoolean(KEY, on))
$backdropImagePath.subscribe(path => persistString(IMAGE_KEY, path))

// Cross-window sync (same pattern as themes/context, store/session, and
// store/translucency): every desktop window (main, HUD, quick entry, pet
// overlay, ...) is another renderer with its own copy of these atoms, seeded
// from localStorage once at load. Changing the backdrop in one window only
// ever repainted that window — a sibling window's `storage` event is the only
// way it hears about the change, so without this listener the backdrop looked
// like it "didn't update" everywhere else until that window was reloaded.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key === KEY) {
      $backdrop.set(storedBoolean(KEY, false))
    } else if (event.key === IMAGE_KEY) {
      $backdropImagePath.set(storedString(IMAGE_KEY))
    }
  })
}

export function setBackdrop(on: boolean) {
  $backdrop.set(on)
}

export function setBackdropImagePath(path: null | string) {
  $backdropImagePath.set(path || null)
}
