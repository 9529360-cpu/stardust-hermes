import { describe, expect, it, vi } from 'vitest'

import type { PluginRecord } from '@/contrib/plugins-store'

import {
  desktopPluginNeedsExplicitTrust,
  desktopPluginTrustSource,
  setDesktopPluginEnabledWithTrust
} from './plugin-desktop-trust'

const copy = {
  confirmLabel: 'Enable trusted code',
  description: (source: string, pinnedSha: string) =>
    `full app authority; source=${source}; sha=${pinnedSha || 'unverified'}`,
  title: (name: string) => `Trust ${name}?`
}

const external = (patch: Partial<PluginRecord> = {}): PluginRecord => ({
  id: 'external-desktop',
  kind: 'disk',
  name: 'External Desktop',
  status: 'disabled',
  ...patch
})

describe('Desktop plugin trust ceremony', () => {
  it('requires an explicit decision for every non-bundled runtime kind', () => {
    expect(desktopPluginNeedsExplicitTrust(external({ kind: 'disk' }))).toBe(true)
    expect(desktopPluginNeedsExplicitTrust(external({ kind: 'runtime' }))).toBe(true)
    expect(desktopPluginNeedsExplicitTrust(external({ kind: 'bundled' }))).toBe(false)
  })

  it('shows package origin and pinned SHA when the unified package recorded them', () => {
    expect(
      desktopPluginTrustSource(
        external({
          packageOrigin: {
            repo: 'https://github.com/example/plugin',
            sha: '0123456789abcdef0123456789abcdef01234567'
          }
        })
      )
    ).toEqual({
      source: 'https://github.com/example/plugin',
      pinnedSha: '0123456789abcdef0123456789abcdef01234567'
    })
  })

  it('falls back to the local plugin.js path for standalone disk code', () => {
    expect(desktopPluginTrustSource(external({ file: '/home/me/.hermes/desktop-plugins/demo/plugin.js' }))).toEqual({
      source: '/home/me/.hermes/desktop-plugins/demo/plugin.js',
      pinnedSha: ''
    })
  })

  it('does not activate external code when the user rejects the trust prompt', async () => {
    const confirm = vi.fn(async () => false)
    const setEnabled = vi.fn(async () => undefined)

    const changed = await setDesktopPluginEnabledWithTrust(external(), 'External Desktop', true, copy, {
      confirm,
      setEnabled
    })

    expect(changed).toBe(false)
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({
        destructive: true,
        title: 'Trust External Desktop?'
      })
    )
    expect(setEnabled).not.toHaveBeenCalled()
  })

  it('activates external code only after affirmative trust', async () => {
    const confirm = vi.fn(async () => true)
    const setEnabled = vi.fn(async () => undefined)
    const record = external({
      packageOrigin: {
        repo: 'https://github.com/example/plugin',
        sha: '0123456789abcdef0123456789abcdef01234567'
      }
    })

    expect(await setDesktopPluginEnabledWithTrust(record, 'External Desktop', true, copy, { confirm, setEnabled })).toBe(
      true
    )
    expect(confirm.mock.calls[0]?.[0].description).toContain('https://github.com/example/plugin')
    expect(confirm.mock.calls[0]?.[0].description).toContain('0123456789abcdef0123456789abcdef01234567')
    expect(setEnabled).toHaveBeenCalledWith('external-desktop', true)
  })

  it('does not prompt when disabling or toggling reviewed bundled code', async () => {
    const confirm = vi.fn(async () => true)
    const setEnabled = vi.fn(async () => undefined)

    await setDesktopPluginEnabledWithTrust(external(), 'External Desktop', false, copy, { confirm, setEnabled })
    await setDesktopPluginEnabledWithTrust(
      external({ id: 'bundled', kind: 'bundled' }),
      'Bundled Plugin',
      true,
      copy,
      { confirm, setEnabled }
    )

    expect(confirm).not.toHaveBeenCalled()
    expect(setEnabled).toHaveBeenNthCalledWith(1, 'external-desktop', false)
    expect(setEnabled).toHaveBeenNthCalledWith(2, 'bundled', true)
  })
})
