import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

const dismiss = vi.fn()
const closeManual = vi.fn()
const onboarding = atom({ localEndpoint: false, manual: false, mode: 'oauth', providers: null })

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

describe('first-run relay entry', () => {
  it('offers relay setup before providers load and navigates to the existing model settings', async () => {
    const { Picker } = await import('./index')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <Picker ctx={{ requestGateway: vi.fn() }} />
      </I18nProvider>
    )

    expect(screen.getByRole('button', { name: '配置模型中转接口' })).toBeTruthy()
    expect(screen.queryByText('Fireworks AI')).toBeNull()
    expect(screen.getByRole('button', { name: /高级选项/ }).getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(screen.getByRole('button', { name: '配置模型中转接口' }))
    expect(dismiss).toHaveBeenCalledOnce()
    expect(window.location.hash).toBe('#/settings?tab=config%3Amodel')
  })

  it('keeps legacy provider choices available only on explicit expansion', async () => {
    const { Picker } = await import('./index')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <Picker ctx={{ requestGateway: vi.fn() }} />
      </I18nProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }))
    expect(screen.getByText('Fireworks AI')).toBeTruthy()
    expect(screen.getByRole('button', { name: '收起' }).getAttribute('aria-expanded')).toBe('true')
  })
})
