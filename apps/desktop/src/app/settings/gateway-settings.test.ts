import { describe, expect, it } from 'vitest'

import { normalizeGatewaySettingsState } from './gateway-settings'

describe('normalizeGatewaySettingsState', () => {
  it('fills missing and undefined persisted fields with canonical defaults', () => {
    const normalized = normalizeGatewaySettingsState({
      mode: 'remote',
      remoteAuthMode: undefined,
      remoteUrl: 'https://gateway.example'
    })

    expect(normalized.mode).toBe('remote')
    expect(normalized.remoteAuthMode).toBe('token')
    expect(normalized.remoteUrl).toBe('https://gateway.example')
    expect(normalized.sshHost).toBe('')
    expect(normalized.sshPort).toBeNull()
    expect(normalized.secureTokenStorage).toBe(true)
  })

  it('returns an independent default state for invalid persisted data', () => {
    const first = normalizeGatewaySettingsState(null)
    const second = normalizeGatewaySettingsState(undefined)

    expect(first).toEqual(second)
    expect(first).not.toBe(second)
  })

  // Stardust no longer offers a built-in "Hermes Cloud" (Nous Portal
  // discovery/login) mode as a first-party product/account surface. A
  // connection an older install saved under mode: 'cloud' is remote-shaped
  // already (a gateway URL + OAuth), so Settings renders it as an ordinary
  // remote connection instead of resurrecting Nous Portal UI for it.
  it('renders a saved "cloud" connection as remote, not as a built-in Nous Portal mode', () => {
    const normalized = normalizeGatewaySettingsState({
      mode: 'cloud',
      remoteAuthMode: 'oauth',
      remoteUrl: 'https://agent.example/hermes'
    })

    expect(normalized.mode).toBe('remote')
    expect(normalized.remoteAuthMode).toBe('oauth')
    expect(normalized.remoteUrl).toBe('https://agent.example/hermes')
  })
})
