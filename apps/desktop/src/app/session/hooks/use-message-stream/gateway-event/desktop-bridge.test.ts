// The agent's `browser(action="connect", target="host")` reaches the desktop as `browser.host.connect`.
// Owner field case 2026-10-12: with no such path the assistant could only ask the owner to open Tools,
// install the extension and press Connect themselves, which took an hour of back-and-forth.
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { handleDesktopBridgeEvent } from './desktop-bridge'
import type { GatewayEventContext } from './types'

const connectHostBrowserForAgent = vi.fn()

vi.mock('@/app/settings/browser-host-connect', () => ({
  connectHostBrowserForAgent: (...args: unknown[]) => connectHostBrowserForAgent(...args)
}))

function connectContext({ active = true, activeSource = true, profileDir = 'Profile 2' } = {}) {
  const payload = { chrome_profile_dir: profileDir } as GatewayEventContext['payload']

  return {
    deps: {} as GatewayEventContext['deps'],
    event: { payload, session_id: 'sess-1', type: 'browser.host.connect' },
    explicitSid: 'sess-1',
    fromActiveSource: () => activeSource,
    isActiveEvent: active,
    occurredAt: 1_700_000_100,
    payload,
    scheduleConfigRefresh: vi.fn(),
    sessionId: 'sess-1'
  } as GatewayEventContext
}

beforeEach(() => connectHostBrowserForAgent.mockReset())

describe('browser.host.connect', () => {
  it('pairs Chrome to the chat on screen with the requested Chrome profile', () => {
    expect(handleDesktopBridgeEvent(connectContext())).toBe(true)
    expect(connectHostBrowserForAgent).toHaveBeenCalledWith('sess-1', 'Profile 2')
  })

  it('never starts a bridge for a chat the user is not looking at', () => {
    expect(handleDesktopBridgeEvent(connectContext({ active: false }))).toBe(true)
    expect(handleDesktopBridgeEvent(connectContext({ activeSource: false }))).toBe(true)
    expect(connectHostBrowserForAgent).not.toHaveBeenCalled()
  })
})
