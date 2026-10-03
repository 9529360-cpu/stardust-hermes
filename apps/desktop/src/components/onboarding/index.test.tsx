import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'
import { makeOAuthProvider } from '@/test/oauth-provider'

import { Picker } from '.'

function setState(overrides: Partial<DesktopOnboardingState> = {}) {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: [
      makeOAuthProvider('anthropic', 'Anthropic Claude'),
      makeOAuthProvider('openai-codex', 'OpenAI Codex / ChatGPT'),
      makeOAuthProvider('nous', 'Nous Portal')
    ],
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false,
    freeTierReady: false,
    ...overrides
  })
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

afterEach(() => {
  cleanup()
  window.location.hash = ''

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false,
    freeTierReady: false
  })
})

describe('onboarding Picker', () => {
  it('offers direct model services without exposing the legacy account catalog', () => {
    setState()
    render(<Picker ctx={ctx} />)

    expect(screen.getByRole('button', { name: 'Add model API' })).toBeTruthy()
    expect(screen.queryByText('Anthropic Claude')).toBeNull()
    expect(screen.queryByText('OpenAI Codex / ChatGPT')).toBeNull()
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.queryByRole('button', { name: /provider accounts/i })).toBeNull()
  })

  it('routes setup to the model API service page and dismisses first-run blocking', () => {
    setState()
    render(<Picker ctx={ctx} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add model API' }))

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.location.hash).toBe('#/settings?tab=config%3Amodel')
  })

  it('offers "choose later" on first run and persists the skip', () => {
    setState()
    render(<Picker ctx={ctx} />)

    fireEvent.click(screen.getByRole('button', { name: "I'll choose a provider later" }))

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.localStorage.getItem('hermes-onboarding-skipped-v1')).toBe('1')
  })

  it('hides "choose later" in manual compatibility mode', () => {
    setState({ manual: true })
    render(<Picker ctx={ctx} />)

    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})
