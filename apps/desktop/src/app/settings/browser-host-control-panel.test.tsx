// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BrowserHostControlPanel } from './browser-host-control-panel'

const mocks = vi.hoisted(() => ({ save: vi.fn(), request: vi.fn(), start: vi.fn(), notify: vi.fn() }))
vi.mock('@/hermes', () => ({ profileScopeKey: () => 'default', saveHermesConfigRecord: mocks.save }))
vi.mock('@/store/gateway', () => ({ requestGatewayForProfile: mocks.request }))
vi.mock('@/store/session', () => ({ $activeSessionId: atom('session') }))
vi.mock('@/store/notifications', () => ({ notify: mocks.notify, notifyError: vi.fn() }))
vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      settings: {
        toolsets: {
          browserHostControl: {
            label: 'Existing Chrome',
            description: 'Pair browser',
            profileLabel: 'Profile',
            statusLabel: 'State',
            status: { inactive: 'Inactive', starting: 'Starting', connected: 'Connected' },
            connect: 'Connect',
            disconnect: 'Disconnect',
            working: 'Working',
            warning: 'Approve extension'
          }
        }
      }
    }
  })
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('BrowserHostControlPanel', () => {
  it('explicit Connect enables only the opted-in setting before preparing; starting does not announce success', async () => {
    const order: string[] = []
    mocks.save.mockImplementation(async () => {
      order.push('save')
    })
    mocks.request.mockImplementation(async (_profile, method) => {
      if (method.endsWith('bridge_status')) {
        return { status: 'inactive' }
      }

      order.push('prepare')

      return { launch_context: { session_id: 'session', grant: 'fixture' } }
    })
    mocks.start.mockImplementation(async () => {
      order.push('start')

      return { status: 'starting', session_id: 'session' }
    })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        browserControl: { start: mocks.start, onStatus: () => () => {}, status: async () => ({ status: 'starting' }) }
      }
    })
    await act(async () => {
      render(<BrowserHostControlPanel />)
    })
    expect(mocks.save).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.change(screen.getByRole('textbox', { name: 'Profile' }), { target: { value: 'Profile 1' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    })
    expect(order).toEqual(['save', 'prepare', 'start'])
    expect(mocks.save).toHaveBeenCalledWith({ browser: { extension_control: { enabled: true } } }, undefined)
    expect(mocks.notify).not.toHaveBeenCalled()
    expect(mocks.request).toHaveBeenCalledWith(
      'default',
      'browser.controller.bridge_prepare',
      expect.objectContaining({ browser_profile_id: 'chrome-UHJvZmlsZSAx' })
    )
    expect(screen.getByRole('button', { name: 'Working' })).toHaveProperty('disabled', true)
  })
})
