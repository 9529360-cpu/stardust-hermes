import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import type * as Gateway from '@/store/gateway'
import { $notifications } from '@/store/notifications'
import { $activeGatewayProfile } from '@/store/profile'

import { connectHostBrowserForAgent } from './browser-host-connect-agent'

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
