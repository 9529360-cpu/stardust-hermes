import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import type * as Gateway from '@/store/gateway'
import { $notifications } from '@/store/notifications'
import { $activeGatewayProfile } from '@/store/profile'

import {
  BROWSER_CONTROL_CAPABILITIES,
  connectHostBrowser,
  connectHostBrowserForAgent,
  InvalidChromeProfileError,
  PLAYWRIGHT_MCP_PACKAGE,
  reconcileBridgeStatus
} from './browser-host-connect'

const saveHermesConfigRecord = vi.fn()
const requestGatewayForProfile = vi.fn()
const start = vi.fn()

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  saveHermesConfigRecord: (...args: unknown[]) => saveHermesConfigRecord(...args)
}))

vi.mock('@/store/gateway', async importOriginal => ({
  ...(await importOriginal<typeof Gateway>()),
  requestGatewayForProfile: (...args: unknown[]) => requestGatewayForProfile(...args)
}))

beforeEach(() => {
  saveHermesConfigRecord.mockReset().mockResolvedValue({ ok: true })
  requestGatewayForProfile.mockReset().mockResolvedValue({ launch_context: { grant: 'one-time' } })
  start.mockReset().mockResolvedValue({ status: 'starting' })
  ;(window as unknown as { stardustDesktop: unknown }).stardustDesktop = { browserControl: { start } }
})

afterEach(() => {
  $notifications.set([])
  $activeGatewayProfile.set('default')
})

describe('connectHostBrowser', () => {
  it('mints a session grant on the profile backend, then starts the bridge with it', async () => {
    await connectHostBrowser({ chromeProfileDir: 'Profile 2', profileKey: 'work', sessionId: 'sess-1' })

    expect(saveHermesConfigRecord).toHaveBeenCalledWith({ browser: { extension_control: { enabled: true } } }, undefined)
    expect(requestGatewayForProfile).toHaveBeenCalledWith('work', 'browser.controller.bridge_prepare', {
      browser_profile_id: expect.stringMatching(/^chrome-/),
      capabilities: BROWSER_CONTROL_CAPABILITIES,
      protocol_version: 1,
      session_id: 'sess-1'
    })
    expect(start).toHaveBeenCalledWith({
      chromeProfileDir: 'Profile 2',
      launchContext: { grant: 'one-time' },
      packageSpec: PLAYWRIGHT_MCP_PACKAGE,
      profile: 'work'
    })
  })

  it('pairs the Default Chrome profile when none is named', async () => {
    await connectHostBrowser({ chromeProfileDir: '  ', profileKey: 'default', sessionId: 'sess-1' })

    expect(start).toHaveBeenCalledWith(expect.objectContaining({ chromeProfileDir: 'Default' }))
  })

  it('refuses a path where a Chrome profile directory name belongs', async () => {
    await expect(
      connectHostBrowser({ chromeProfileDir: '..\\..\\Users', profileKey: 'default', sessionId: 'sess-1' })
    ).rejects.toBeInstanceOf(InvalidChromeProfileError)
    expect(requestGatewayForProfile).not.toHaveBeenCalled()
  })
})

describe('connectHostBrowserForAgent', () => {
  it('connects through the active profile and tells the user on screen', async () => {
    $activeGatewayProfile.set('work')

    await connectHostBrowserForAgent('sess-1', '')

    expect(requestGatewayForProfile).toHaveBeenCalledWith('work', 'browser.controller.bridge_prepare', expect.anything())
    expect(saveHermesConfigRecord).toHaveBeenCalledWith(expect.anything(), 'work')
    expect($notifications.get().map(n => n.kind)).toEqual(['info'])
  })

  it('shows a bridge that reports a failed start', async () => {
    start.mockResolvedValue({ error: 'Packaged Playwright bridge resource is unavailable', status: 'error' })

    await connectHostBrowserForAgent('sess-1', 'Default')

    expect($notifications.get().some(n => n.kind === 'error')).toBe(true)
  })

  it('shows the failure instead of failing silently', async () => {
    start.mockRejectedValue(new Error('Browser control requires a local loopback backend'))

    await connectHostBrowserForAgent('sess-1', 'Default')

    expect($notifications.get().some(n => n.kind === 'error')).toBe(true)
  })
})

describe('reconcileBridgeStatus', () => {
  // A panel opened while the agent's bridge is still starting: the backend has no registration yet.
  it('shows the bridge Electron is starting for this chat over an empty backend answer', () => {
    const local = { session_id: 'sess-1', status: 'starting' as const }

    expect(reconcileBridgeStatus({ status: 'inactive' }, local, 'sess-1')).toBe(local)
  })

  it("never shows another chat's bridge", () => {
    const backend = { status: 'inactive' as const }

    expect(reconcileBridgeStatus(backend, { session_id: 'other', status: 'connected' }, 'sess-1')).toBe(backend)
  })

  it('keeps the backend answer once the bridge has registered', () => {
    const backend = { controller_id: 'c', status: 'connected' as const }

    expect(reconcileBridgeStatus(backend, { session_id: 'sess-1', status: 'starting' }, 'sess-1')).toBe(backend)
  })
})
