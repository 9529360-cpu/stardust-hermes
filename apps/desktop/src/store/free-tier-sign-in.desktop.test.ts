import { afterEach, describe, expect, it } from 'vitest'

import { $freeTierSignIn, openFreeTierSignIn } from './free-tier-sign-in'

const originalDesktopBridge = window.hermesDesktop

afterEach(() => {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: originalDesktopBridge
  })
  $freeTierSignIn.set({ status: 'closed' })
})

describe('openFreeTierSignIn', () => {
  it('keeps the retired built-in account flow closed in Stardust Desktop', () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {}
    })

    openFreeTierSignIn()

    expect($freeTierSignIn.get()).toEqual({ status: 'closed' })
  })

  it('keeps the shared non-Desktop compatibility behavior temporarily', () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: undefined
    })

    openFreeTierSignIn()

    expect($freeTierSignIn.get()).toEqual({ status: 'requested' })
  })
})
