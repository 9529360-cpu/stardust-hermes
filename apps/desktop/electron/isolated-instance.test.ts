import { describe, expect, it, vi } from 'vitest'

import { createHudSnapShortcut } from './hud-snap-shortcut'
import { createDesktopInstancePolicy } from './isolated-instance'
import { createQuickEntryShortcut, type GlobalShortcutLike, sanitizeQuickEntrySettings } from './quick-entry'

function systemIntegrations() {
  const held = new Set<string>()

  const globalShortcut: GlobalShortcutLike = {
    isRegistered: vi.fn((accelerator: string) => held.has(accelerator)),
    register: vi.fn((accelerator: string, _callback: () => void) => {
      held.add(accelerator)

      return true
    }),
    unregister: vi.fn((accelerator: string) => {
      held.delete(accelerator)
    })
  }

  const protocol = vi.fn<(scheme: string) => boolean>().mockReturnValue(true)

  return { globalShortcut, held, protocol }
}

describe('desktop instance system integration policy', () => {
  it('blocks protocol and both real shortcut controllers for an isolated instance', () => {
    const policy = createDesktopInstancePolicy({ STARDUST_DESKTOP_ISOLATED: '1' })
    const { globalShortcut, protocol } = systemIntegrations()
    const quickEntry = createQuickEntryShortcut(globalShortcut, vi.fn())
    const hud = createHudSnapShortcut(globalShortcut, vi.fn())
    const settings = sanitizeQuickEntrySettings(undefined)

    policy.runSystemIntegration(() => {
      protocol('stardust')
      protocol('hermes')
    })
    expect(quickEntry.apply(policy.quickEntrySettings(settings))).toMatchObject({
      error: null,
      registered: false
    })
    // Reopening HUD and changing the configured chord must stay isolated too.
    policy.runSystemIntegration(() => hud.register())
    quickEntry.apply(policy.quickEntrySettings({ enabled: true, shortcut: 'Alt+J' }))
    policy.runSystemIntegration(() => hud.register())
    quickEntry.dispose()
    hud.dispose()

    expect(policy.isolated).toBe(true)
    expect(settings.enabled).toBe(true)
    expect(protocol).not.toHaveBeenCalled()
    expect(globalShortcut.isRegistered).not.toHaveBeenCalled()
    expect(globalShortcut.register).not.toHaveBeenCalled()
    expect(globalShortcut.unregister).not.toHaveBeenCalled()
  })

  it.each([undefined, '', '0', 'true', 'false'])('preserves ordinary registration for flag %s', flag => {
    const policy = createDesktopInstancePolicy({ STARDUST_DESKTOP_ISOLATED: flag })
    const { globalShortcut, held, protocol } = systemIntegrations()
    const quickEntry = createQuickEntryShortcut(globalShortcut, vi.fn())
    const hud = createHudSnapShortcut(globalShortcut, vi.fn())
    const settings = sanitizeQuickEntrySettings(undefined)

    const registered = policy.runSystemIntegration(() => protocol('stardust'))
    const quickState = quickEntry.apply(policy.quickEntrySettings(settings))
    const hudState = policy.runSystemIntegration(() => hud.register())

    expect(policy.isolated).toBe(false)
    expect(registered).toBe(true)
    expect(protocol).toHaveBeenCalledWith('stardust')
    expect(policy.quickEntrySettings(settings)).toBe(settings)
    expect(quickState.registered).toBe(true)
    expect(hudState).toBe(true)
    expect(held.size).toBe(2)

    quickEntry.dispose()
    hud.dispose()
    expect(held.size).toBe(0)
    expect(globalShortcut.unregister).toHaveBeenCalledTimes(2)
  })

  it('does not infer isolation from a data directory, app name or session client', () => {
    const policy = createDesktopInstancePolicy({
      STARDUST_DESKTOP_USER_DATA_DIR: 'task-user-data',
      STARDUST_DESKTOP_APP_NAME: 'task-app',
      HERMES_DESKTOP: '1'
    })

    const register = vi.fn()

    policy.runSystemIntegration(register)

    expect(policy.isolated).toBe(false)
    expect(register).toHaveBeenCalledOnce()
  })
})
