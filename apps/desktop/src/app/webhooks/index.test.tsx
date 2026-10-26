import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Hermes from '@/hermes'
import { en } from '@/i18n/en'
import { stubMenuDomApis, stubResizeObserver } from '@/test/jsdom'

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
  stubResizeObserver()
  stubMenuDomApis()
})

afterEach(cleanup)

describe('webhook delivery targets', () => {
  it('offers retained messaging platforms and omits retired adapters', async () => {
    getWebhooks.mockResolvedValue({ enabled: true, subscriptions: [] })
    mount()

    fireEvent.click(await screen.findByRole('button', { name: en.webhooks.newSubscription }))
    fireEvent.click(screen.getByRole('combobox', { name: en.webhooks.fieldDeliver }))

    for (const label of [
      en.webhooks.deliverOptions.telegram,
      en.webhooks.deliverOptions.whatsapp,
      en.webhooks.deliverOptions.whatsapp_cloud,
      en.webhooks.deliverOptions.weixin,
      en.webhooks.deliverOptions.github_comment
    ]) {
      expect(screen.getByRole('option', { name: label })).toBeTruthy()
    }
    for (const retired of ['Discord', 'Slack', 'Email']) {
      expect(screen.queryByRole('option', { name: retired })).toBeNull()
    }
  })
})


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
