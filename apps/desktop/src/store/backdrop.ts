import { atom } from 'nanostores'

import { persistBoolean, persistString, storedBoolean, storedString } from '@/lib/storage'

const KEY = 'hermes.desktop.backdrop.v1'
const IMAGE_KEY = 'stardust.desktop.backdrop-image.v1'

/** Whether the optional image backdrop renders behind the chat transcript. */
export const $backdrop = atom(storedBoolean(KEY, false))

/**
 * Device-local image path for the chat backdrop.
 *
 * Only the path is persisted. The renderer reads the bytes through Electron on
 * this machine when it paints; the image is never sent to the gateway, model,
 * Memory, or profile state.
 */
export const $backdropImagePath = atom<null | string>(storedString(IMAGE_KEY))

$backdrop.subscribe(on => persistBoolean(KEY, on))
$backdropImagePath.subscribe(path => persistString(IMAGE_KEY, path))

export function setBackdrop(on: boolean) {
  $backdrop.set(on)
}

export function setBackdropImagePath(path: null | string) {
  $backdropImagePath.set(path || null)
}
