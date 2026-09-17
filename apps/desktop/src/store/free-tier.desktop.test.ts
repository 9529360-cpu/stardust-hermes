import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  $freeTierRoute,
  $freeTierStatus,
  ackFreeTierNotice,
  refreshFreeTierStatus,
  setFreeTierRoute
} from './free-tier'

const originalDesktopBridge = window.hermesDesktop

const reportedFreeTier = {
  available: true,
  enabled: true,
  has_guest: true,
  label: 'Nous · free tier',
  model: 'nous/welcome',
  notice_pending: true
}

afterEach(() => {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: originalDesktopBridge
  })
  $freeTierStatus.set(null)
  $freeTierRoute.set(null)
})

describe('free-tier host policy', () => {
  it('ignores inherited guest state and RPCs in Stardust Desktop', async () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {}
    })
    const requestGateway = vi.fn().mockResolvedValue(reportedFreeTier)

    expect(await refreshFreeTierStatus(requestGateway)).toBeNull()
    expect($freeTierStatus.get()).toBeNull()
    expect(requestGateway).not.toHaveBeenCalled()

    setFreeTierRoute(true)
    expect($freeTierRoute.get()).toBeNull()

    expect(await ackFreeTierNotice(requestGateway)).toBe(true)
    expect(requestGateway).not.toHaveBeenCalled()
  })

  it('keeps the shared non-Desktop compatibility behavior temporarily', async () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: undefined
    })
    const requestGateway = vi.fn().mockResolvedValue(reportedFreeTier)

    expect(await refreshFreeTierStatus(requestGateway)).toEqual(reportedFreeTier)
    expect($freeTierStatus.get()).toEqual(reportedFreeTier)
    expect(requestGateway).toHaveBeenCalledWith('free_tier.status')

    setFreeTierRoute(true)
    expect($freeTierRoute.get()).toBe(true)
  })
})
