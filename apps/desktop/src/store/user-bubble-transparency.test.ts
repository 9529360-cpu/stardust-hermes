import { TRANSLUCENCY_MAX, TRANSLUCENCY_MIN } from '@hermes/shared/translucency'
import { afterEach, describe, expect, it } from 'vitest'

import { $userBubbleTransparency, setUserBubbleTransparency } from './user-bubble-transparency'

const KEY = 'hermes.desktop.user-bubble-transparency.v1'
const NON_DEFAULT = Math.round((TRANSLUCENCY_MIN + TRANSLUCENCY_MAX) / 2)

afterEach(() => {
  setUserBubbleTransparency(TRANSLUCENCY_MIN)
  window.localStorage.removeItem(KEY)
  document.documentElement.style.removeProperty('--user-bubble-keep')
})

describe('setUserBubbleTransparency', () => {
  it('persists a non-default value and paints the CSS variable', () => {
    setUserBubbleTransparency(NON_DEFAULT)

    expect(window.localStorage.getItem(KEY)).toBe(String(NON_DEFAULT))
    expect(document.documentElement.style.getPropertyValue('--user-bubble-keep')).toBe(
      `${TRANSLUCENCY_MAX - NON_DEFAULT}%`
    )
  })

  it('clears persistence and the CSS variable at the default (byte-identical to untouched)', () => {
    setUserBubbleTransparency(NON_DEFAULT)
    setUserBubbleTransparency(TRANSLUCENCY_MIN)

    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect(document.documentElement.style.getPropertyValue('--user-bubble-keep')).toBe('')
  })
})

// Same class of bug as the custom backdrop image (store/backdrop.ts): each
// desktop window is its own renderer with its own copy of this atom, seeded
// once at load. Dragging the lever in one window must not strand every other
// open window's bubbles at the old fill until reloaded.
describe('cross-window sync', () => {
  it("adopts a sibling window's value", () => {
    setUserBubbleTransparency(TRANSLUCENCY_MIN)
    window.localStorage.setItem(KEY, String(NON_DEFAULT))

    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: String(NON_DEFAULT) }))

    expect($userBubbleTransparency.get()).toBe(NON_DEFAULT)
    expect(document.documentElement.style.getPropertyValue('--user-bubble-keep')).toBe(
      `${TRANSLUCENCY_MAX - NON_DEFAULT}%`
    )
  })

  it('ignores storage events for unrelated keys', () => {
    setUserBubbleTransparency(NON_DEFAULT)
    window.localStorage.setItem('hermes.desktop.zoom.v1', '2')

    window.dispatchEvent(new StorageEvent('storage', { key: 'hermes.desktop.zoom.v1', newValue: '2' }))

    expect($userBubbleTransparency.get()).toBe(NON_DEFAULT)
  })

  it('a sibling reset (key removed) syncs back to the default', () => {
    setUserBubbleTransparency(NON_DEFAULT)
    window.localStorage.removeItem(KEY)

    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: null }))

    expect($userBubbleTransparency.get()).toBe(TRANSLUCENCY_MIN)
  })
})
