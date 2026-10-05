import { beforeEach, describe, expect, it, vi } from 'vitest'

const KEY = 'hermes.desktop.reasoning.collapsedByDefault.v2'
const V1_KEY = 'hermes.desktop.reasoning.collapsedByDefault'

const loadStore = () => import('./reasoning-disclosure')

describe('reasoning collapsed-by-default preference', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  it('collapses thinking when the user never chose, without storing that default', async () => {
    const store = await loadStore()

    expect(store.$reasoningCollapsedByDefault.get()).toBe(true)
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('does not read the v1 value, which every launch wrote whether or not the user chose it', async () => {
    window.localStorage.setItem(V1_KEY, 'false')

    const store = await loadStore()

    expect(store.$reasoningCollapsedByDefault.get()).toBe(true)
  })

  it('stores an explicit choice and keeps it after a reload', async () => {
    const first = await loadStore()

    first.setReasoningCollapsedByDefault(false)

    expect(window.localStorage.getItem(KEY)).toBe('false')

    vi.resetModules()
    const reloaded = await loadStore()

    expect(reloaded.$reasoningCollapsedByDefault.get()).toBe(false)
  })
})
