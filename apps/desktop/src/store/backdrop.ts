import { atom } from 'nanostores'

import { persistBoolean, persistString, storedBoolean, storedString } from '@/lib/storage'

const KEY = 'hermes.desktop.backdrop.v1'
const IMAGE_KEY = 'stardust.desktop.backdrop-image.v1'
const OPACITY_KEY = 'stardust.desktop.backdrop-opacity.v1'

/** File types the image picker offers for the window backdrop. */
export const BACKDROP_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] as const

/** Opacity of the custom backdrop image, in whole percent. The floor keeps a chosen image visible. */
export const BACKDROP_OPACITY_MIN = 10
export const BACKDROP_OPACITY_MAX = 100
export const BACKDROP_OPACITY_DEFAULT = 100

/** Clamp and round a stored or slider value to a usable opacity percent. */
export function normalizeBackdropOpacity(value: unknown): number {
  const percent = typeof value === 'number' ? value : Number(value)

  if (!Number.isFinite(percent)) {
    return BACKDROP_OPACITY_DEFAULT
  }

  return Math.min(BACKDROP_OPACITY_MAX, Math.max(BACKDROP_OPACITY_MIN, Math.round(percent)))
}

const readBackdropOpacity = (): number => normalizeBackdropOpacity(storedString(OPACITY_KEY) ?? BACKDROP_OPACITY_DEFAULT)

/** Whether the optional image backdrop renders behind the desktop window. */
export const $backdrop = atom(storedBoolean(KEY, false))

/** Device-local opacity of the custom backdrop image (the bundled artwork keeps its own fixed look). */
export const $backdropOpacity = atom(readBackdropOpacity())

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
$backdropOpacity.subscribe(percent => persistString(OPACITY_KEY, String(percent)))

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
    } else if (event.key === OPACITY_KEY) {
      $backdropOpacity.set(readBackdropOpacity())
    }
  })
}

export function setBackdrop(on: boolean) {
  $backdrop.set(on)
}

export function setBackdropImagePath(path: null | string) {
  $backdropImagePath.set(path || null)
}

export function setBackdropOpacity(percent: number) {
  $backdropOpacity.set(normalizeBackdropOpacity(percent))
}
