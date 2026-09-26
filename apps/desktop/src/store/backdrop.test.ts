import { afterEach, describe, expect, it } from 'vitest'

import { $backdrop, $backdropImagePath, setBackdrop, setBackdropImagePath } from './backdrop'

const KEY = 'hermes.desktop.backdrop.v1'
const IMAGE_KEY = 'stardust.desktop.backdrop-image.v1'

afterEach(() => {
  setBackdrop(false)
  setBackdropImagePath(null)
  window.localStorage.removeItem(KEY)
  window.localStorage.removeItem(IMAGE_KEY)
})

describe('setBackdrop / setBackdropImagePath', () => {
  it('persists the toggle and the image path', () => {
    setBackdropImagePath('/Users/me/Pictures/wallpaper.png')
    setBackdrop(true)

    expect(window.localStorage.getItem(KEY)).toBe('true')
    expect(window.localStorage.getItem(IMAGE_KEY)).toBe('/Users/me/Pictures/wallpaper.png')
  })

  it('clears the persisted path on null', () => {
    setBackdropImagePath('/tmp/one.jpg')
    setBackdropImagePath(null)

    expect($backdropImagePath.get()).toBeNull()
    expect(window.localStorage.getItem(IMAGE_KEY)).toBeNull()
  })
})

// Every desktop window (main, HUD, quick entry, pet overlay, ...) is another
// renderer with its own copy of these atoms, seeded from localStorage once at
// load — changing the backdrop in one window only ever repainted that window.
// A sibling window's `storage` event is the only way this window hears about
// a change made elsewhere (same pattern as themes/context and
// store/translucency).
describe('cross-window sync', () => {
  it("adopts a sibling window's image path change", () => {
    setBackdropImagePath('/tmp/old.jpg')
    window.localStorage.setItem(IMAGE_KEY, '/tmp/new.jpg')

    window.dispatchEvent(new StorageEvent('storage', { key: IMAGE_KEY, newValue: '/tmp/new.jpg' }))

    expect($backdropImagePath.get()).toBe('/tmp/new.jpg')
  })

  it("adopts a sibling window's on/off toggle", () => {
    setBackdrop(false)
    window.localStorage.setItem(KEY, 'true')

    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: 'true' }))

    expect($backdrop.get()).toBe(true)
  })

  it('ignores storage events for unrelated keys', () => {
    setBackdrop(true)
    setBackdropImagePath('/tmp/keep.jpg')
    window.localStorage.setItem('hermes.desktop.zoom.v1', '2')

    window.dispatchEvent(new StorageEvent('storage', { key: 'hermes.desktop.zoom.v1', newValue: '2' }))

    expect($backdrop.get()).toBe(true)
    expect($backdropImagePath.get()).toBe('/tmp/keep.jpg')
  })

  it('a cleared image path (key removed) syncs to null', () => {
    setBackdropImagePath('/tmp/gone.jpg')
    window.localStorage.removeItem(IMAGE_KEY)

    window.dispatchEvent(new StorageEvent('storage', { key: IMAGE_KEY, newValue: null }))

    expect($backdropImagePath.get()).toBeNull()
  })
})
