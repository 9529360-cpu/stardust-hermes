import { afterEach, describe, expect, it, vi } from 'vitest'

const current = 'hermes.desktop.statusbarHidden.v3'
const previous = 'hermes.desktop.statusbarHidden.v2'
const wholeBar = 'hermes.desktop.statusbarVisible.v2'

afterEach(() => {
  window.localStorage.removeItem(current)
  window.localStorage.removeItem(previous)
  window.localStorage.removeItem(wholeBar)
  vi.resetModules()
})

describe('bottom usage entry preference migration', () => {
  it('shows the bar and usage entry on a new install', async () => {
    vi.resetModules()
    const prefs = await import('./statusbar-prefs')

    expect(prefs.$statusbarVisible.get()).toBe(true)
    expect(prefs.$statusbarHiddenIds.get()).not.toContain('context-usage')
  })

  it('keeps an explicit hidden bar and other v2 customizations', async () => {
    window.localStorage.setItem(wholeBar, 'false')
    window.localStorage.setItem(previous, JSON.stringify(['cron', 'context-usage', 'plugin-item']))
    vi.resetModules()
    const prefs = await import('./statusbar-prefs')

    expect(prefs.$statusbarVisible.get()).toBe(false)
    expect(prefs.$statusbarHiddenIds.get()).toEqual(['cron', 'plugin-item'])
  })

  it('keeps an explicit v3 choice to hide usage on the next launch', async () => {
    window.localStorage.setItem(previous, JSON.stringify(['cron', 'context-usage']))
    window.localStorage.setItem(current, JSON.stringify(['context-usage', 'webhooks']))
    vi.resetModules()
    const prefs = await import('./statusbar-prefs')

    expect(prefs.$statusbarHiddenIds.get()).toEqual(['context-usage', 'webhooks'])
  })
})
