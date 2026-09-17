/**
 * Guided onboarding never supplies or requires a hidden account. A configured
 * guide may own the foreground, but a guide whose provider disappeared must
 * yield immediately to provider recovery instead of trapping the user in a
 * conversation that cannot run.
 */
import { expect, it, vi } from 'vitest'

import type * as storageModule from '@/lib/storage'

const storage = vi.hoisted(() => new Map<string, string>())
const WARNING = "No API key configured for provider 'openai'. First message will fail."

vi.mock('@/lib/onboarding-enabled', () => ({ isOnboardingEnabled: () => true }))
vi.mock('@/lib/storage', async importOriginal => ({
  ...(await importOriginal<typeof storageModule>()),
  readKey: (key: string) => storage.get(key) ?? null,
  writeKey: (key: string, value: string | null) => {
    if (value === null) {
      storage.delete(key)
    } else {
      storage.set(key, value)
    }
  }
}))

async function load(phase: string) {
  storage.clear()
  storage.set('hermes-onboarding-phase-v1', phase)
  vi.resetModules()

  const gate = await import('./onboarding-gate')
  const onboarding = await import('./onboarding')

  return { gate, onboarding }
}

function setProviderReady(
  onboarding: Awaited<ReturnType<typeof load>>['onboarding'],
  ready: boolean
) {
  onboarding.$desktopOnboarding.set({
    ...onboarding.$desktopOnboarding.get(),
    configured: ready
  })
  onboarding.$desktopRuntimeVerified.set(ready)
}

it.each(['cinematic', 'guided', 'handoff'])(
  'provider recovery outranks an active guide without a provider (%s)',
  async phase => {
    const { onboarding } = await load(phase)

    setProviderReady(onboarding, false)
    onboarding.requestDesktopOnboarding('No inference provider is configured.')

    expect(onboarding.$desktopOnboarding.get().requested).toBe(true)
  }
)

it('does not treat a cached configured bit as runtime proof', async () => {
  const { onboarding } = await load('guided')

  onboarding.$desktopOnboarding.set({
    ...onboarding.$desktopOnboarding.get(),
    configured: true
  })
  onboarding.$desktopRuntimeVerified.set(false)
  onboarding.requestDesktopOnboarding('No inference provider is configured.')

  expect(onboarding.$desktopOnboarding.get().requested).toBe(true)
})

it.each(['cinematic', 'guided', 'handoff'])('a configured guide suppresses a passive picker (%s)', async phase => {
  const { onboarding } = await load(phase)

  setProviderReady(onboarding, true)
  onboarding.requestDesktopOnboarding('No inference provider is configured.')

  expect(onboarding.$desktopOnboarding.get().requested).toBe(false)
})

it.each(['cinematic', 'guided', 'handoff'])(
  'credential recovery is retained when an active guide loses its provider (%s)',
  async phase => {
    const { onboarding } = await load(phase)

    setProviderReady(onboarding, false)
    onboarding.requestDesktopOnboardingForCredentialWarning(WARNING)

    expect(onboarding.consumePendingCredentialWarning()).toBe(WARNING)
  }
)

it.each(['cinematic', 'guided', 'handoff'])('a configured guide drops passive credential noise (%s)', async phase => {
  const { onboarding } = await load(phase)

  setProviderReady(onboarding, true)
  onboarding.requestDesktopOnboardingForCredentialWarning(WARNING)

  expect(onboarding.consumePendingCredentialWarning()).toBeNull()
})

it.each(['idle', 'skipped', 'done'])('outside the guide the picker opens as before (%s)', async phase => {
  const { onboarding } = await load(phase)

  onboarding.requestDesktopOnboarding('No inference provider is configured.')

  expect(onboarding.$desktopOnboarding.get().requested).toBe(true)
})
