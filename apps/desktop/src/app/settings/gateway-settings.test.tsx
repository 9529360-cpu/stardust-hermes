import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Collect the component graph before the behavioral test deadline starts.
import { GatewaySettings } from './gateway-settings'

const { registry, activeId, selectConnection } = vi.hoisted(() => ({
  registry: { value: null as any },
  activeId: { value: 'saved-b' },
  selectConnection: vi.fn().mockResolvedValue(undefined)
}))

vi.mock('@nanostores/react', () => ({ useStore: (store: any) => store.value }))
vi.mock('@/store/connections', () => ({
  $connectionsRegistry: registry,
  $activeConnectionId: activeId,
  refreshConnectionsRegistry: vi.fn().mockResolvedValue(null),
  selectConnection,
  setConnectionsRegistry: vi.fn()
}))
vi.mock('./connections-registry', async importOriginal => ({
  ...(await importOriginal<any>()),
  ConnectionsRegistrySection: () => null
}))
const getConnectionConfig = vi.fn()
const saveConnectionConfig = vi.fn()

// This test owns the machine-level GatewaySettings contract. The managed SSH
// update section mounted below the registry has its own focused coverage
// (store/managed-updates.test.ts); keep its store subscriptions out of this
// single-purpose test.
vi.mock('./managed-updates-section', () => ({ ManagedUpdatesSection: () => null }))

const localConnection = {
  envOverride: false,
  mode: 'local',
  remoteAuthMode: 'token',
  remoteOauthConnected: false,
  remoteTokenPreview: null,
  remoteTokenSet: false,
  remoteUrl: ''
}

beforeEach(() => {
  getConnectionConfig.mockResolvedValue(localConnection)
  saveConnectionConfig.mockResolvedValue(localConnection)
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { getConnectionConfig, saveConnectionConfig }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('GatewaySettings', () => {
  // Stardust no longer ships a built-in "Hermes Cloud" (Nous Portal
  // discovery/login) mode. A connection an older install saved under
  // mode: 'cloud' is remote-shaped already (a gateway URL + OAuth), so
  // Settings renders it as an ordinary Remote connection — no first-party
  // Nous discovery/sign-in UI, and no `desktop.cloud` bridge call.
  it('renders a saved Cloud connection as Remote, with no built-in Nous discovery UI', async () => {
    getConnectionConfig.mockResolvedValue({ ...localConnection, mode: 'cloud', remoteUrl: 'https://a.example' })
    registry.value = null
    render(<GatewaySettings embedded />)

    const remoteCard = (await screen.findByText('Remote gateway')).closest('button')!

    expect(remoteCard.className).toContain('border-primary')
    expect(screen.queryByText('Hermes Cloud')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Connect', exact: true })).toBeNull()
    expect((window.hermesDesktop as any).cloud).toBeUndefined()
  })
  it('does not offer a Cloud mode tile', async () => {
    render(<GatewaySettings embedded />)
    await screen.findByText('Local gateway')

    expect(screen.queryByText('Hermes Cloud')).toBeNull()
  })
  it('loads the machine-level connection config (no profile scoping)', async () => {
    render(<GatewaySettings />)
    expect(await screen.findByText('Local gateway')).toBeTruthy()
    expect(
      screen.getByText('Start a private Hermes backend on localhost. This is the default and works offline.')
    ).toBeTruthy()

    // The page manages the machine's gateway connections; it must load the
    // global config, never a per-profile override.
    await waitFor(() => expect(getConnectionConfig).toHaveBeenCalledWith(null))
    expect(getConnectionConfig).not.toHaveBeenCalledWith(expect.any(String))

    // The legacy per-profile scope switcher must not render.
    expect(screen.queryByText('Applies to')).toBeNull()
    expect(screen.queryByText('All profiles')).toBeNull()
    expect(screen.queryByText('Use default gateway')).toBeNull()
  })
})
