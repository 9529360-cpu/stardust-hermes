import type { WorkItem } from '@hermes/shared'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { $subagentsBySession } from '@/store/subagents'

import { ComposerStatusStack } from './index'

const requestGateway = vi.hoisted(() => vi.fn(async () => ({ scoped: '', work: [] as WorkItem[] })))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

vi.mock('@/lib/use-enter-animation', () => ({ useEnterAnimation: () => undefined }))

vi.stubGlobal(
  'ResizeObserver',
  class {
    disconnect() {}
    observe() {}
    unobserve() {}
  }
)

const cronRun = (overrides: Partial<WorkItem> = {}): WorkItem => ({
  id: 'cron:exec-1',
  kind: 'cron',
  title: 'Nightly digest',
  status: 'running',
  started_at: 100,
  updated_at: 101,
  detail: {},
  ...overrides
})

function renderStack() {
  return render(
    <MemoryRouter>
      <ComposerStatusStack queue={null} sessionId="owner" />
    </MemoryRouter>
  )
}

afterEach(() => {
  cleanup()
  requestGateway.mockReset()
  requestGateway.mockResolvedValue({ scoped: '', work: [] } as never)
  $subagentsBySession.set({})
})

it('shows a running scheduled run read-only, with no stop or steer control', async () => {
  requestGateway.mockResolvedValue({ scoped: '', work: [cronRun()] } as never)
  const { container } = renderStack()

  await waitFor(() =>
    expect(requestGateway).toHaveBeenCalledWith('cron.executions.list', { profile: '', limit: 20 })
  )
  fireEvent.click(await screen.findByRole('button', { name: /1 Background/ }))

  expect(screen.getByText('Nightly digest')).toBeTruthy()
  expect(screen.getByText('Scheduled run')).toBeTruthy()

  const row = container.querySelector('[data-slot="composer-cron-run"]')

  expect(row).not.toBeNull()
  expect(row?.querySelector('button, input')).toBeNull()
})

it('keeps the roster hidden when no cron run is running', async () => {
  requestGateway.mockResolvedValue({ scoped: '', work: [cronRun({ status: 'completed' })] } as never)
  renderStack()

  await waitFor(() => expect(requestGateway).toHaveBeenCalled())

  expect(screen.queryByText('Nightly digest')).toBeNull()
  expect(screen.queryByRole('button', { name: /Background/ })).toBeNull()
})

it('drops a response scoped to another profile instead of showing its runs', async () => {
  requestGateway.mockResolvedValue({ scoped: 'coder', work: [cronRun()] } as never)
  renderStack()

  await waitFor(() => expect(requestGateway).toHaveBeenCalled())

  expect(screen.queryByText('Nightly digest')).toBeNull()
  expect(screen.queryByRole('button', { name: /Background/ })).toBeNull()
})

it('shows nothing and does not throw when the cron list RPC fails', async () => {
  requestGateway.mockRejectedValue(new Error('gateway unavailable') as never)
  renderStack()

  await waitFor(() => expect(requestGateway).toHaveBeenCalled())

  expect(screen.queryByRole('button', { name: /Background/ })).toBeNull()
})
