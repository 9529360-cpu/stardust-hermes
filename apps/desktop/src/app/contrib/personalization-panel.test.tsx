// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $backdropOpacity, BACKDROP_OPACITY_DEFAULT, setBackdropOpacity } from '@/store/backdrop'
import { setPetEnabled } from '@/store/pet-gallery'

import { PersonalizationPanel } from './personalization-panel'

const { setMode } = vi.hoisted(() => ({ setMode: vi.fn() }))

vi.mock('@/themes/context', () => ({
  useTheme: () => ({ mode: 'light', setMode })
}))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway: vi.fn() })
}))

vi.mock('@/store/pet-gallery', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  setPetEnabled: vi.fn().mockResolvedValue(true)
}))

afterEach(() => {
  cleanup()
  setMode.mockClear()
  vi.mocked(setPetEnabled).mockClear()
  setBackdropOpacity(BACKDROP_OPACITY_DEFAULT)
})

function renderPanel(onOpenAppearance = vi.fn()) {
  render(
    <I18nProvider configClient={null} initialLocale="zh">
      <PersonalizationPanel onOpenAppearance={onOpenAppearance} />
    </I18nProvider>
  )

  return onOpenAppearance
}

describe('PersonalizationPanel', () => {
  it('switches the colour mode through the same theme setter as Settings', () => {
    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '暗色' }))

    expect(setMode).toHaveBeenCalledWith('dark')
  })

  it('writes the device-local backdrop opacity from the slider', () => {
    renderPanel()

    fireEvent.change(screen.getByRole('slider', { name: '图片不透明度' }), { target: { value: '40' } })

    expect($backdropOpacity.get()).toBe(40)
    expect(screen.getByText('40%')).toBeTruthy()
  })

  it('turns the desktop pet on through the pet gallery', () => {
    renderPanel()

    fireEvent.click(screen.getByRole('switch', { name: '桌面宠物' }))

    expect(setPetEnabled).toHaveBeenCalledWith(
      expect.any(Function),
      true,
      expect.objectContaining({ noneAvailable: expect.any(String) })
    )
  })

  it('hands the detailed controls to Settings > Appearance', () => {
    const onOpenAppearance = renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '更多外观设置' }))

    expect(onOpenAppearance).toHaveBeenCalledTimes(1)
  })
})
