import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $pluginDecisions, $pluginRecords } from '@/contrib/plugins-store'
import { $agentPlugins, $agentPluginsStatus } from '@/store/agent-plugins'
import { $pluginInstallRequest, closePluginInstallRequest } from '@/store/plugin-install-request'

import { PluginsTab } from './plugins-tab'

const requestGateway = vi.fn(async () => ({ plugins: [] }))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

describe('PluginsTab', () => {
  beforeEach(() => {
    $pluginDecisions.set({})
    $pluginRecords.set({})
    $agentPlugins.set([])
    $agentPluginsStatus.set('ready')
    closePluginInstallRequest()
    requestGateway.mockClear()
  })

  afterEach(cleanup)

  it('lists the scoped profile agent plugins with toggles', () => {
    $agentPlugins.set([
      {
        description: 'A test plugin',
        key: 'demo-plugin',
        name: 'demo-plugin',
        source: 'git',
        status: 'enabled',
        version: '1.0.0'
      }
    ])

    render(<PluginsTab profile="workbot" />)

    expect(screen.getByText('demo-plugin')).toBeTruthy()
    expect(screen.getByRole('switch', { name: 'Agent: demo-plugin' }).getAttribute('aria-checked')).toBe('true')
  })

  it('hides bundled plugins (managed from their own surfaces)', () => {
    $agentPlugins.set([
      {
        description: '',
        key: 'image_gen/fal',
        name: 'fal',
        source: 'bundled',
        status: 'enabled',
        version: ''
      }
    ])

    render(<PluginsTab profile={null} />)

    expect(screen.queryByText('fal')).toBeNull()
    expect(screen.getByText(/No plugins yet/)).toBeTruthy()
  })

  it('renders a unified package as ONE row with a Desktop switch and an Agent switch', () => {
    $pluginRecords.set({
      media: { id: 'media', name: 'Media Studio', kind: 'disk', status: 'loaded', packageName: 'hermes-media-studio' }
    })
    $agentPlugins.set([
      {
        description: '',
        key: 'hermes-media-studio',
        name: 'hermes-media-studio',
        source: 'git',
        status: 'disabled',
        version: '1'
      }
    ])

    render(<PluginsTab profile="workbot" scopeLabel="workbot" />)

    expect(screen.getAllByTestId(/^plugin-row-/)).toHaveLength(1)
    expect(screen.getByText('Agent + Desktop')).toBeTruthy()
    expect(screen.getByRole('switch', { name: 'Desktop: Media Studio' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('switch', { name: 'Agent: Media Studio' }).getAttribute('aria-checked')).toBe('false')
    expect(screen.getAllByText('Agent in workbot').length).toBeGreaterThan(0)
  })

  it('requires explicit trust before enabling external Desktop code and shows recorded provenance', async () => {
    const sha = 'a'.repeat(40)
    const repo = 'https://github.com/example/external-desktop.git'

    $pluginRecords.set({
      external: {
        id: 'external',
        name: 'External Tool',
        kind: 'disk',
        decisionId: 'external-slot',
        status: 'disabled',
        file: '/tmp/desktop-plugins/external/plugin.js',
        packageOrigin: { repo, sha }
      }
    })

    render(<PluginsTab profile={null} />)

    fireEvent.click(screen.getByRole('switch', { name: 'Desktop: External Tool' }))

    expect($pluginDecisions.get()['external-slot']).not.toBe(true)
    expect($pluginDecisions.get().external).toBeUndefined()
    expect(screen.getByText('Trust and enable External Tool?')).toBeTruthy()
    expect(screen.getByText(repo)).toBeTruthy()
    expect(screen.getByText(sha)).toBeTruthy()
    expect(screen.getAllByText(/same app authority/i).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Trust & enable' }))

    await waitFor(() => expect($pluginDecisions.get()['external-slot']).toBe(true))
    expect($pluginDecisions.get().external).toBeUndefined()
  })
  it('offers "Install here" for a desktop half whose agent half is not in the selected profile', async () => {
    $pluginRecords.set({
      media: {
        id: 'media',
        name: 'Media Studio',
        kind: 'disk',
        status: 'loaded',
        packageName: 'hermes-media-studio',
        packageOrigin: { repo: 'https://github.com/NousResearch/hermes-media-studio.git', sha: 'abc' }
      }
    })

    render(<PluginsTab profile="workbot" scopeLabel="workbot" />)

    expect(screen.queryByRole('switch', { name: /^Agent:/ })).toBeNull()
    screen.getByRole('button', { name: 'Install here' }).click()
    // Pre-filled from the package marker: repo + pinned sha, agent half only.
    await waitFor(() => {
      expect($pluginInstallRequest.get()).toMatchObject({
        legacyHint: 'agent',
        profile: 'workbot',
        repo: 'https://github.com/NousResearch/hermes-media-studio.git',
        sha: 'abc'
      })
    })
  })

  it('disables "Install here" when the package has no known origin (hand-copied folder)', () => {
    $pluginRecords.set({
      media: { id: 'media', name: 'Media Studio', kind: 'disk', status: 'loaded', packageName: 'hermes-media-studio' }
    })

    render(<PluginsTab profile="workbot" scopeLabel="workbot" />)

    expect((screen.getByRole('button', { name: 'Install here' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('loads the plugin list scoped to the selected profile', () => {
    render(<PluginsTab profile="workbot" />)

    expect(requestGateway).toHaveBeenCalledWith(
      'plugins.manage',
      expect.objectContaining({ action: 'list', profile: 'workbot' })
    )
  })

  it('toggles by canonical key through plugins.manage', async () => {
    $agentPlugins.set([
      {
        description: '',
        key: 'image_gen/legacy',
        name: 'Legacy plugin',
        source: 'user',
        status: 'disabled',
        version: '0.20.0'
      }
    ])
    requestGateway.mockResolvedValueOnce({
      ok: true,
      plugin: { key: 'image_gen/legacy', name: 'Legacy plugin', status: 'enabled' }
    } as never)

    render(<PluginsTab profile={null} />)

    screen.getByRole('switch', { name: 'Agent: Legacy plugin' }).click()

    await waitFor(() =>
      expect(requestGateway).toHaveBeenCalledWith(
        'plugins.manage',
        expect.objectContaining({ action: 'toggle', key: 'image_gen/legacy', enable: true })
      )
    )
  })

  it('renders keyless rows read-only (no name-addressed toggle RPC)', () => {
    // Name-addressed toggles flip every same-named plugin across category
    // dirs — pre-contract-v6 rows must never reach the RPC.
    $agentPlugins.set([
      {
        description: 'Returned by a pre-key backend',
        name: 'Legacy plugin',
        source: 'user',
        status: 'disabled',
        version: '0.20.0'
      }
    ])

    render(<PluginsTab profile={null} />)

    const toggle = screen.getByRole('switch', { name: 'Agent: Legacy plugin' })

    expect(toggle.hasAttribute('disabled') || toggle.getAttribute('aria-disabled') === 'true').toBe(true)

    toggle.click()

    expect(requestGateway).not.toHaveBeenCalledWith('plugins.manage', expect.objectContaining({ action: 'toggle' }))
  })
})

describe('PluginsTab without an upstream catalog', () => {
  afterEach(cleanup)

  it('offers no upstream plugin catalog browser', () => {
    const { container } = render(<PluginsTab profile={null} />)

    expect(screen.queryByRole('button', { name: 'Browse' })).toBeNull()
    expect(screen.queryByText('Plugin catalog')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
  })
})

describe('PluginsTab installed-plugin updates', () => {
  beforeEach(() => {
    $agentPlugins.set([])
    $agentPluginsStatus.set('ready')
    closePluginInstallRequest()
    requestGateway.mockClear()
  })

  afterEach(cleanup)

  it('shows an Update chip when the catalog pin moved past the installed SHA', () => {
    $agentPlugins.set([
      {
        catalog_name: 'demo-weather',
        catalog_sha: 'b'.repeat(40),
        catalog_tier: 'community',
        description: '',
        installed_sha: 'a'.repeat(40),
        key: 'demo-weather',
        name: 'demo-weather',
        source: 'git',
        status: 'enabled',
        update_available: true,
        version: '1.0.0'
      }
    ])

    render(<PluginsTab profile={null} />)

    expect(screen.getByRole('button', { name: `Update to ${'b'.repeat(8)}` })).toBeTruthy()
  })

  it('re-pins through plugins.manage update when the chip is clicked', async () => {
    $agentPlugins.set([
      {
        catalog_name: 'demo-weather',
        catalog_sha: 'b'.repeat(40),
        catalog_tier: 'community',
        description: '',
        installed_sha: 'a'.repeat(40),
        key: 'demo-weather',
        name: 'demo-weather',
        source: 'git',
        status: 'enabled',
        update_available: true,
        version: '1.0.0'
      }
    ])
    requestGateway.mockResolvedValue({ ok: true, unchanged: false, plugins: [] } as never)

    render(<PluginsTab profile="workbot" />)

    screen.getByRole('button', { name: `Update to ${'b'.repeat(8)}` }).click()

    await waitFor(() =>
      expect(requestGateway).toHaveBeenCalledWith(
        'plugins.manage',
        expect.objectContaining({ action: 'update', name: 'demo-weather', profile: 'workbot' })
      )
    )
  })
})
