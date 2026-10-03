import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

const dismiss = vi.fn()
const closeManual = vi.fn()
const onboarding = atom({ localEndpoint: false, manual: false, mode: 'oauth', providers: null as unknown })

vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: onboarding,
  dismissFirstRunOnboarding: dismiss,
  closeManualOnboarding: closeManual
}))
vi.mock('@/hermes', () => ({ getGlobalModelOptions: vi.fn().mockResolvedValue({ providers: [] }) }))

afterEach(() => {
  cleanup()
  onboarding.set({ localEndpoint: false, manual: false, mode: 'oauth', providers: null })
  window.location.hash = ''
  vi.clearAllMocks()
})

describe('first-run model API entry', () => {
  it('offers direct model API setup before provider inventory loads', async () => {
    const { Picker } = await import('./index')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <Picker ctx={{ requestGateway: vi.fn() }} />
      </I18nProvider>
    )

    expect(screen.getByRole('button', { name: '添加模型 API' })).toBeTruthy()
    expect(screen.queryByText('Fireworks AI')).toBeNull()
    expect(screen.queryByRole('button', { name: /高级选项/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '添加模型 API' }))
    expect(dismiss).toHaveBeenCalledOnce()
    expect(window.location.hash).toBe('#/settings?tab=config%3Amodel')
  })

  it('does not expose legacy provider choices even when provider inventory already exists', async () => {
    onboarding.set({
      localEndpoint: false,
      manual: false,
      mode: 'oauth',
      providers: [{ id: 'anthropic', name: 'Anthropic Claude' }]
    })

    const { Picker } = await import('./index')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <Picker ctx={{ requestGateway: vi.fn() }} />
      </I18nProvider>
    )

    expect(screen.getByRole('button', { name: '添加模型 API' })).toBeTruthy()
    expect(screen.queryByText('Anthropic Claude')).toBeNull()
    expect(screen.queryByText('Fireworks AI')).toBeNull()
    expect(screen.queryByRole('button', { name: /高级选项/ })).toBeNull()
  })
})
