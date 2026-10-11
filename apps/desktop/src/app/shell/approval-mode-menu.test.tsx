import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { StatusbarControls } from '@/app/shell/statusbar-controls'
import { I18nProvider } from '@/i18n'
import { $approvalModes } from '@/store/approval-mode'
import { stubMenuDomApis, stubResizeObserver } from '@/test/jsdom'

import { useApprovalModeStatusbarItem } from './approval-mode-menu'

beforeAll(() => {
  stubResizeObserver()
  stubMenuDomApis()
})

afterEach(() => {
  cleanup()
  $approvalModes.set({})
})

function Harness({
  gatewayOpen = true,
  profile = 'default',
  requestGateway
}: {
  gatewayOpen?: boolean
  profile?: string
  requestGateway: (method: string, params?: Record<string, unknown>) => Promise<unknown>
}) {
  const item = useApprovalModeStatusbarItem(profile, requestGateway, gatewayOpen)

  return (
    <MemoryRouter>
      <StatusbarControls items={[item]} />
    </MemoryRouter>
  )
}

describe('approval mode statusbar item', () => {
  it('uses the shared statusbar menu trigger without a nested bespoke button', async () => {
    render(<Harness requestGateway={vi.fn(async () => ({ value: 'smart' }))} />)

    const statusbar = screen.getByRole('contentinfo')
    const trigger = await within(statusbar).findByRole('button', { name: /smart/i })
    expect(within(statusbar).getAllByRole('button')).toHaveLength(1)

    fireEvent.pointerDown(trigger, { button: 0 })

    expect(await screen.findByRole('menuitemradio', { name: /manual/i })).toBeTruthy()
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu')
    expect(screen.getByRole('menuitemradio', { name: /smart/i })).toBeTruthy()
    expect(screen.getByRole('menuitemradio', { name: /off/i })).toBeTruthy()
  })

  it('writes the selected mode through the gateway and updates its shared trigger label', async () => {
    const requestGateway = vi.fn(async (_method, params) => ({ value: params?.value ?? 'smart' }))
    render(<Harness profile="work" requestGateway={requestGateway} />)

    fireEvent.pointerDown(await screen.findByRole('button', { name: /smart/i }), { button: 0 })
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /manual/i }))

    await waitFor(() => {
      expect(requestGateway).toHaveBeenCalledWith('config.set', { key: 'approvals.mode', value: 'manual' })
      expect(screen.getByRole('button', { name: /manual/i })).toBeTruthy()
    })
  })

  it('names the approvals subject on the bar and shows the mode beside it', () => {
    // A bare "Off" on the bar read as a close action; the subject says what it controls.
    $approvalModes.set({ default: 'off' })
    render(<Harness requestGateway={vi.fn(() => new Promise<never>(() => undefined))} />)

    const trigger = screen.getByRole('button', { name: /Approvals/ })

    expect(within(trigger).getByText('Approvals')).toBeTruthy()
    expect(within(trigger).getByText('Off')).toBeTruthy()
  })

  it('shows a reading label, never a default mode, until the mode has been read', () => {
    render(<Harness profile="unread-profile" requestGateway={vi.fn(() => new Promise<never>(() => undefined))} />)

    const trigger = screen.getByRole('button', { name: /Approvals/ })

    expect(within(trigger).getByText('Reading')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /smart/i })).toBeNull()
  })

  it('shows an explicit unknown label when the mode cannot be read', async () => {
    render(
      <Harness
        profile="failed-profile"
        requestGateway={vi.fn(async () => {
          throw new Error('request timed out')
        })}
      />
    )

    const trigger = await screen.findByRole('button', { name: /Unknown/ })

    expect(within(trigger).getByText('Approvals')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /smart/i })).toBeNull()
  })

  it('reads the mode once the gateway opens, not while it is closed', async () => {
    const requestGateway = vi.fn(async () => ({ value: 'off' }))
    const { rerender } = render(<Harness gatewayOpen={false} profile="reopened-profile" requestGateway={requestGateway} />)

    expect(requestGateway).not.toHaveBeenCalled()

    rerender(<Harness gatewayOpen profile="reopened-profile" requestGateway={requestGateway} />)

    expect(await screen.findByRole('button', { name: /Off/ })).toBeTruthy()
    expect(requestGateway).toHaveBeenCalledWith('config.get', { key: 'approvals.mode' })
  })

  it('renders the shared trigger and menu in the active locale', async () => {
    $approvalModes.set({ default: 'smart' })
    const response = new Promise<never>(() => undefined)
    render(
      <I18nProvider configClient={null} initialLocale="ja">
        <Harness requestGateway={vi.fn(() => response)} />
      </I18nProvider>
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: /スマート/ }), { button: 0 })

    expect(await screen.findByText('必要な場合にのみ確認します')).toBeTruthy()
    expect(screen.getByText('承認プロンプトなしで実行します')).toBeTruthy()
  })
})
