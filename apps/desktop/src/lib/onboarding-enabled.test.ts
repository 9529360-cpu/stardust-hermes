import { afterEach, describe, expect, it } from 'vitest'

import { isOnboardingEnabled } from './onboarding-enabled'

const originalDesktopBridge = window.hermesDesktop

afterEach(() => {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: originalDesktopBridge
  })
})

describe('isOnboardingEnabled', () => {
  it('uses the account-independent guided onboarding flag', () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { guidedOnboardingEnabled: true, guestOnboardingEnabled: false }
    })

    expect(isOnboardingEnabled()).toBe(true)
  })

  it('does not let the retired guest-account flag enable the guide', () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { guidedOnboardingEnabled: false, guestOnboardingEnabled: true }
    })

    expect(isOnboardingEnabled()).toBe(false)
  })
})
