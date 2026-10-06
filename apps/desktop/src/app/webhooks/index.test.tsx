import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'

import { WebhooksView } from './index'

const { getWebhooks } = vi.hoisted(() => ({ getWebhooks: vi.fn() }))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  getWebhooks: (...args: unknown[]) => getWebhooks(...args)
}))

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <WebhooksView onClose={vi.fn()} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  getWebhooks.mockReset()
})

afterEach(cleanup)

describe('webhook list load failure', () => {
  it('does not show an unknown empty list and recovers through Retry', async () => {
    getWebhooks
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ enabled: false, subscriptions: [] })

    mount()

    expect(await screen.findByText(en.webhooks.loadFailed)).toBeTruthy()
    expect(screen.queryByText(en.webhooks.empty)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: en.common.retry }))

    await waitFor(() => expect(screen.getByText(en.webhooks.empty)).toBeTruthy())
    expect(screen.queryByText(en.webhooks.loadFailed)).toBeNull()
    expect(getWebhooks).toHaveBeenCalledTimes(2)
  })
})
